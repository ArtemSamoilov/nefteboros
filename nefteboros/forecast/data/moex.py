"""MOEX ISS API fetcher для российских акций и индексов.

API: https://iss.moex.com/iss/  (публичный REST, без auth, без VPN).
Документация: https://www.moex.com/a2193

Покрывает:
  moexog  — MOEX Oil & Gas Index (отраслевой индекс, движок=stock/market=index)
  gazp    — Газпром, обычные акции (TQBR режим)
  nvtk    — Новатэк, обычные акции (TQBR режим)

Особенность ISS API: candles endpoint возвращает по 500 точек на запрос —
для 5-летнего daily-ряда (~1250 точек) нужна пагинация через `start=N`.

См. ADR-0012 §«Discovery — что не работает в открытых источниках».
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

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


MOEX_ISS_BASE = "https://iss.moex.com/iss"

# Per-asset MOEX market metadata.
# Tuple = (market, board)  где board=None для индексов.
#   market="shares" → engines/stock/markets/shares/boards/<board>/securities/<sec>
#   market="index"  → engines/stock/markets/index/securities/<sec>
MOEX_ASSET_MARKET: dict[str, tuple[str, Optional[str]]] = {
    "moexog": ("index", None),
    "gazp":   ("shares", "TQBR"),
    "nvtk":   ("shares", "TQBR"),
}

# Лимит точек на одну страницу (определяется ISS API, не нами)
PAGE_SIZE = 500


# =============================================================================
# Public API
# =============================================================================


def fetch_moex(
    asset_id: str,
    *,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp] = None,
    use_cache: bool = True,
    ttl_hours: int = 24,
    max_retries: int = 2,
) -> pd.Series:
    """Получить дневные close-котировки актива через MOEX ISS.

    Прозрачная пагинация: подтягиваем все страницы по 500 точек, склеиваем,
    сохраняем единым рядом в кеше.

    Args:
        asset_id: один из MOEX_ASSET_MARKET (moexog, gazp, nvtk).
                  Должен иметь primary_source=MOEX_ISS в registry.

    Returns:
        pd.Series с DatetimeIndex (UTC, normalized к началу дня), close в RUB
        (для акций) или index points (для индексов). name=asset_id.
    """
    meta = get_asset(asset_id)
    if meta.primary_source != DataSource.MOEX_ISS:
        raise ValueError(
            f"{asset_id!r} primary_source={meta.primary_source.value!r}, "
            "not MOEX ISS. Use the appropriate fetcher."
        )
    if asset_id not in MOEX_ASSET_MARKET:
        raise ValueError(
            f"No MOEX market mapping for asset_id={asset_id!r}. "
            f"Known: {sorted(MOEX_ASSET_MARKET.keys())}"
        )
    if not meta.primary_ticker:
        raise ValueError(f"{asset_id!r}: primary_ticker is empty in registry")

    cache_key = f"moex__{asset_id}"

    # 1. Свежий кеш
    if use_cache and is_fresh(cache_key, ttl_hours=ttl_hours):
        cached = read_cache(cache_key)
        if cached is not None and not cached.empty:
            logger.debug(
                "moex: %s served from fresh cache (n=%d, age=%.1fh)",
                cache_key, len(cached), cache_age_hours(cache_key) or 0.0,
            )
            return _filter_window(cached, since, until)

    # 2. Live с retries
    market, board = MOEX_ASSET_MARKET[asset_id]
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            series = _fetch_paginated(
                security=meta.primary_ticker,
                market=market,
                board=board,
                since=since,
                until=until,
            )
            series.name = cache_key
            if use_cache:
                write_cache(cache_key, series)
            logger.info(
                "moex: %s fetched live (n=%d, %s..%s)",
                cache_key, len(series),
                series.index.min().date(), series.index.max().date(),
            )
            return series
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "moex: %s attempt %d/%d failed (%s); retry in %ds",
                    asset_id, attempt + 1, max_retries + 1, e, wait,
                )
                time.sleep(wait)

    # 3. Stale cache fallback
    if use_cache:
        cached = read_cache(cache_key)
        if cached is not None and not cached.empty:
            age = cache_age_hours(cache_key) or float("inf")
            logger.warning(
                "moex: %s live failed (%s); falling back to STALE cache "
                "(n=%d, age=%.1fh)",
                cache_key, last_err, len(cached), age,
            )
            return _filter_window(cached, since, until)

    raise RuntimeError(
        f"moex: live and cache both unavailable for {cache_key!r}. "
        f"Last error: {last_err}"
    ) from last_err


# =============================================================================
# Internal
# =============================================================================


def _candles_url(*, security: str, market: str, board: Optional[str]) -> str:
    if market == "shares":
        if not board:
            raise ValueError("board is required for market='shares'")
        return (
            f"{MOEX_ISS_BASE}/engines/stock/markets/shares/boards/{board}"
            f"/securities/{security}/candles.json"
        )
    if market == "index":
        return (
            f"{MOEX_ISS_BASE}/engines/stock/markets/index/securities/{security}"
            f"/candles.json"
        )
    raise ValueError(f"Unknown market={market!r}")


def _fetch_paginated(
    *,
    security: str,
    market: str,
    board: Optional[str],
    since: pd.Timestamp,
    until: Optional[pd.Timestamp],
) -> pd.Series:
    """Постранично собрать candles по интервалу = day.

    interval=24 — daily candles. Возвращаем close-цену.
    """
    import requests

    url = _candles_url(security=security, market=market, board=board)
    until_norm = until if until is not None else pd.Timestamp.now(tz="UTC").normalize()

    base_params: dict[str, Any] = {
        "from": _date_str(since),
        "till": _date_str(until_norm),
        "interval": 24,  # daily
    }

    all_dates: list[pd.Timestamp] = []
    all_closes: list[float] = []
    start = 0
    safety = 0  # против бесконечной пагинации
    while True:
        params = {**base_params, "start": start}
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"MOEX HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        candles = payload.get("candles", {})
        cols = candles.get("columns", [])
        rows = candles.get("data", [])
        if not rows:
            break
        if "close" not in cols or "begin" not in cols:
            raise RuntimeError(
                f"MOEX unexpected columns for {security}: {cols}"
            )
        ci = cols.index("close")
        bi = cols.index("begin")
        for row in rows:
            close = row[ci]
            begin = row[bi]
            if close is None or begin is None:
                continue
            all_closes.append(float(close))
            all_dates.append(pd.Timestamp(begin, tz="UTC"))
        if len(rows) < PAGE_SIZE:
            break  # последняя страница
        start += len(rows)
        safety += 1
        if safety > 50:
            raise RuntimeError(
                f"MOEX pagination safety break for {security} at start={start}"
            )

    if not all_dates:
        raise RuntimeError(f"MOEX empty data for {security}")

    series = pd.Series(all_closes, index=pd.DatetimeIndex(all_dates))
    series.index = series.index.normalize()
    series = series.groupby(level=0).last().sort_index()
    return series


def _filter_window(
    series: pd.Series,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp],
) -> pd.Series:
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


def _date_str(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


__all__ = ["fetch_moex", "MOEX_ASSET_MARKET", "MOEX_ISS_BASE"]
