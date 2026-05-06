"""yfinance fetcher для daily-цен (нефть/газ/экзогены).

Покрывает primary-источники для:
  brent      (BZ=F)
  wti        (CL=F)
  henry_hub  (NG=F)
  ttf        (TTF=F)

Плюс экзогены для SARIMAX:
  dxy        (DX-Y.NYB) — US Dollar Index

yfinance — нестабильный публичный API (rate-limits, occasional 429s, формат
изменялся). Стратегия:
  1. Если кеш свежий (TTL) — отдаём из него, не дёргаем сеть.
  2. Live с двумя retries (exponential backoff).
  3. Если все retries упали — возвращаем КЕШ КАК ЕСТЬ (даже если устарел) с warning.
  4. Если и кеша нет — RuntimeError. Не выдумываем данные.

См. ADR-0012 §«Источники данных».
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd

from nefteboros.forecast.cache import (
    cache_age_hours,
    is_fresh,
    read_cache,
    write_cache,
)
from nefteboros.forecast.registry import get_asset
from nefteboros.forecast.schema import DataSource

logger = logging.getLogger(__name__)


# =============================================================================
# Экзогены, которых нет в ASSET_REGISTRY (DXY и т.д.)
# =============================================================================

EXOG_TICKERS: dict[str, str] = {
    "dxy": "DX-Y.NYB",  # US Dollar Index — топ-1 macro-предиктор для нефти
    # На будущее: futures curve points (BZ=F front vs M2 vs M6) — отдельный fetcher.
}


# =============================================================================
# Public API
# =============================================================================


def fetch_yfinance(
    asset_id: str,
    *,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp] = None,
    use_cache: bool = True,
    ttl_hours: int = 24,
    max_retries: int = 2,
) -> pd.Series:
    """Получить дневные Close-цены актива из ASSET_REGISTRY через yfinance.

    Args:
        asset_id: один из ID реестра (brent, wti, henry_hub, ttf).
                  Asset должен иметь primary_source=YFINANCE.
        since: начало истории (включительно).
        until: конец (по умолчанию — сегодня UTC).
        use_cache: проверять/писать кеш.
        ttl_hours: какой кеш считается «свежим».
        max_retries: попыток live-запроса перед уходом в кеш-fallback.

    Returns:
        pd.Series с DatetimeIndex (UTC, normalized к началу дня), float64,
        отсортированный по возрастанию даты, без NaN. name=asset_id.

    Raises:
        ValueError: asset_id неизвестен или его primary_source != yfinance.
        RuntimeError: все live-попытки упали И кеш отсутствует.
    """
    meta = get_asset(asset_id)
    if meta.primary_source != DataSource.YFINANCE:
        raise ValueError(
            f"{asset_id!r} primary_source={meta.primary_source.value!r}, "
            "not yfinance. Use the appropriate fetcher."
        )
    if not meta.primary_ticker:
        raise ValueError(f"{asset_id!r}: primary_ticker is empty in registry")

    return _fetch_ticker(
        ticker=meta.primary_ticker,
        cache_key=asset_id,
        since=since,
        until=until,
        use_cache=use_cache,
        ttl_hours=ttl_hours,
        max_retries=max_retries,
    )


def fetch_exog(
    name: str,
    *,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp] = None,
    use_cache: bool = True,
    ttl_hours: int = 24,
    max_retries: int = 2,
) -> pd.Series:
    """Получить экзогенный ряд (DXY и т.п.) через yfinance."""
    if name not in EXOG_TICKERS:
        raise ValueError(
            f"Unknown exog name {name!r}. "
            f"Valid: {sorted(EXOG_TICKERS.keys())}"
        )
    return _fetch_ticker(
        ticker=EXOG_TICKERS[name],
        cache_key=f"exog__{name}",
        since=since,
        until=until,
        use_cache=use_cache,
        ttl_hours=ttl_hours,
        max_retries=max_retries,
    )


# =============================================================================
# Internal
# =============================================================================


def _fetch_ticker(
    *,
    ticker: str,
    cache_key: str,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp],
    use_cache: bool,
    ttl_hours: int,
    max_retries: int,
) -> pd.Series:
    # 1. Свежий кеш — сразу отдаём
    if use_cache and is_fresh(cache_key, ttl_hours=ttl_hours):
        cached = read_cache(cache_key)
        if cached is not None and not cached.empty:
            logger.debug(
                "yfinance: %s served from fresh cache (n=%d, age=%.1fh)",
                cache_key, len(cached), cache_age_hours(cache_key) or 0.0,
            )
            return _filter_window(cached, since, until)

    # 2. Live с retries
    until_norm = until if until is not None else pd.Timestamp.now(tz="UTC").normalize()
    last_err: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            series = _yfinance_history(ticker, since=since, until=until_norm)
            series.name = cache_key
            if use_cache:
                write_cache(cache_key, series)
            logger.info(
                "yfinance: %s fetched live (n=%d, %s..%s)",
                cache_key, len(series), series.index.min().date(), series.index.max().date(),
            )
            return series
        except Exception as e:  # noqa: BLE001 — yfinance бросает разные типы
            last_err = e
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "yfinance: %s attempt %d/%d failed (%s); retry in %ds",
                    ticker, attempt + 1, max_retries + 1, e, wait,
                )
                time.sleep(wait)

    # 3. Live упал — пытаемся stale cache как honest fallback
    if use_cache:
        cached = read_cache(cache_key)
        if cached is not None and not cached.empty:
            age = cache_age_hours(cache_key) or float("inf")
            logger.warning(
                "yfinance: %s live failed (%s); falling back to STALE cache "
                "(n=%d, age=%.1fh)",
                cache_key, last_err, len(cached), age,
            )
            return _filter_window(cached, since, until)

    # 4. Ничего нет — честная ошибка
    raise RuntimeError(
        f"yfinance: live and cache both unavailable for {cache_key!r} "
        f"(ticker={ticker!r}). Last error: {last_err}"
    ) from last_err


def _yfinance_history(
    ticker: str,
    *,
    since: pd.Timestamp,
    until: pd.Timestamp,
) -> pd.Series:
    """Один live-запрос. Бросает RuntimeError на пустых/невалидных результатах."""
    import yfinance as yf  # отложенный импорт — чистая ошибка, если пакет не стоит

    data = yf.Ticker(ticker).history(
        start=since,
        end=until,
        interval="1d",
        auto_adjust=False,
        actions=False,
    )
    if data is None or data.empty:
        raise RuntimeError(f"empty data for ticker {ticker!r}")
    if "Close" not in data.columns:
        raise RuntimeError(f"no Close column in yfinance result for {ticker!r}")

    series = data["Close"].astype(float).copy()
    # Нормализация индекса: все даты UTC, normalized к началу дня
    idx = pd.to_datetime(series.index, utc=True)
    series.index = idx.normalize()
    series = series.dropna().sort_index()

    if series.empty:
        raise RuntimeError(f"all-NaN Close for ticker {ticker!r}")
    return series


def _filter_window(
    series: pd.Series,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp],
) -> pd.Series:
    """Срезаем по окну [since, until]. Если кеш шире — мы получаем подмножество."""
    out = series
    if since is not None:
        s = pd.Timestamp(since)
        if s.tzinfo is None:
            s = s.tz_localize("UTC")
        out = out[out.index >= s]
    if until is not None:
        u = pd.Timestamp(until)
        if u.tzinfo is None:
            u = u.tz_localize("UTC")
        out = out[out.index <= u]
    return out


__all__ = ["fetch_yfinance", "fetch_exog", "EXOG_TICKERS"]
