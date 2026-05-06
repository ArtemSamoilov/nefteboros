"""EIA fetcher — spot prices и фундаментальные ряды (запасы, STEO).

API v2: https://api.eia.gov/v2/<dataset>/data/
Документация: https://www.eia.gov/opendata/documentation.php

Ключ берётся из env `EIA_API_KEY`. Если не задан — fetcher сразу падает
с ясной ошибкой (не молча возвращает None).

Использование:
  - **secondary spot** для активов, у которых registry.AssetMeta.secondary_source = EIA:
    fetch_eia_for_asset("brent")  # spot RBRTE.D, не futures как в yfinance.
  - **экзогены SARIMAX**: fetch_eia_inventory("us_crude") — WCESTUS1 weekly.

Особенности:
  - EIA публикует daily-spot с задержкой ~7 дней — поэтому primary всё ещё
    yfinance, EIA — для верификации и как baseline для spread-моделей
    Urals/ESPO (см. ADR-0012, раздел про futures-vs-spot).
  - Weekly inventories публикуются по средам (US ET) — сдвиг локали учитываем
    мягко: используем дату публикации как индекс, без смещения вперёд.
  - Monthly STEO-серии возможны как third-party reference (не закладываем
    в primary, чтобы не путать пользователя).

См. ADR-0012 §«Источники данных».
"""

from __future__ import annotations

import logging
import os
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


EIA_BASE_URL = "https://api.eia.gov/v2"


# =============================================================================
# Маппинг asset_id → (dataset endpoint, series_id, frequency)
# =============================================================================
# secondary_ticker в registry — это «PET.RBRTE.D» в нотации EIA-bulk;
# для v2 API нужен dataset endpoint и series_id отдельно. Этот маппинг
# держим здесь, чтобы registry не загромождать transport-деталями.

EIA_ASSET_ENDPOINTS: dict[str, tuple[str, str, str]] = {
    # asset_id : (dataset, series_id, frequency)
    "brent":     ("petroleum/pri/spt/data",     "RBRTE",    "daily"),
    "wti":       ("petroleum/pri/spt/data",     "RWTC",     "daily"),
    "henry_hub": ("natural-gas/pri/fut/data",   "RNGWHHD",  "daily"),
}

# Экзогены (для SARIMAX) — не в ASSET_REGISTRY.
EIA_EXOG_ENDPOINTS: dict[str, tuple[str, str, str]] = {
    # name : (dataset, series_id, frequency)
    "us_crude_inventory":    ("petroleum/stoc/wstk/data",   "WCESTUS1",  "weekly"),
    "us_gasoline_inventory": ("petroleum/stoc/wstk/data",   "WGTSTUS1",  "weekly"),
    "us_natgas_storage":     ("natural-gas/stor/wkly/data", "NW2_EPG0_SWO_R48_BCF", "weekly"),
}


# =============================================================================
# Public API
# =============================================================================


def fetch_eia_for_asset(
    asset_id: str,
    *,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp] = None,
    use_cache: bool = True,
    ttl_hours: int = 24,
    max_retries: int = 2,
) -> pd.Series:
    """Получить EIA-ряд для актива (secondary spot).

    Args:
        asset_id: должен быть в EIA_ASSET_ENDPOINTS (brent/wti/henry_hub).
                  И в registry.AssetMeta.secondary_source = EIA.
    """
    if asset_id not in EIA_ASSET_ENDPOINTS:
        raise ValueError(
            f"No EIA endpoint mapping for asset_id={asset_id!r}. "
            f"Known: {sorted(EIA_ASSET_ENDPOINTS.keys())}"
        )
    meta = get_asset(asset_id)
    if meta.secondary_source != DataSource.EIA:
        raise ValueError(
            f"{asset_id!r} secondary_source={meta.secondary_source}, "
            "not EIA. Check registry."
        )

    dataset, series_id, frequency = EIA_ASSET_ENDPOINTS[asset_id]
    return _fetch(
        dataset=dataset,
        series_id=series_id,
        frequency=frequency,
        cache_key=f"eia__{asset_id}",
        since=since,
        until=until,
        use_cache=use_cache,
        ttl_hours=ttl_hours,
        max_retries=max_retries,
    )


def fetch_eia_inventory(
    name: str,
    *,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp] = None,
    use_cache: bool = True,
    ttl_hours: int = 24,
    max_retries: int = 2,
) -> pd.Series:
    """Получить weekly-серию запасов/хранилищ — экзоген для SARIMAX."""
    if name not in EIA_EXOG_ENDPOINTS:
        raise ValueError(
            f"Unknown EIA exog name {name!r}. "
            f"Known: {sorted(EIA_EXOG_ENDPOINTS.keys())}"
        )
    dataset, series_id, frequency = EIA_EXOG_ENDPOINTS[name]
    return _fetch(
        dataset=dataset,
        series_id=series_id,
        frequency=frequency,
        cache_key=f"eia__exog__{name}",
        since=since,
        until=until,
        use_cache=use_cache,
        ttl_hours=ttl_hours,
        max_retries=max_retries,
    )


# =============================================================================
# Internal
# =============================================================================


def _api_key() -> str:
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "EIA_API_KEY env var not set. "
            "Get a free key at https://www.eia.gov/opendata/register.php "
            "and put it in .env."
        )
    return key


def _fetch(
    *,
    dataset: str,
    series_id: str,
    frequency: str,
    cache_key: str,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp],
    use_cache: bool,
    ttl_hours: int,
    max_retries: int,
) -> pd.Series:
    # 1. Свежий кеш
    if use_cache and is_fresh(cache_key, ttl_hours=ttl_hours):
        cached = read_cache(cache_key)
        if cached is not None and not cached.empty:
            logger.debug(
                "eia: %s served from fresh cache (n=%d, age=%.1fh)",
                cache_key, len(cached), cache_age_hours(cache_key) or 0.0,
            )
            return _filter_window(cached, since, until)

    # 2. Live с retries
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            series = _eia_request(
                dataset=dataset,
                series_id=series_id,
                frequency=frequency,
                since=since,
                until=until,
            )
            series.name = cache_key
            if use_cache:
                write_cache(cache_key, series)
            logger.info(
                "eia: %s fetched live (n=%d, %s..%s)",
                cache_key, len(series),
                series.index.min().date(), series.index.max().date(),
            )
            return series
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "eia: %s/%s attempt %d/%d failed (%s); retry in %ds",
                    dataset, series_id, attempt + 1, max_retries + 1, e, wait,
                )
                time.sleep(wait)

    # 3. Stale cache fallback
    if use_cache:
        cached = read_cache(cache_key)
        if cached is not None and not cached.empty:
            age = cache_age_hours(cache_key) or float("inf")
            logger.warning(
                "eia: %s live failed (%s); falling back to STALE cache "
                "(n=%d, age=%.1fh)",
                cache_key, last_err, len(cached), age,
            )
            return _filter_window(cached, since, until)

    raise RuntimeError(
        f"eia: live and cache both unavailable for {cache_key!r} "
        f"(dataset={dataset}, series={series_id}). Last error: {last_err}"
    ) from last_err


def _eia_request(
    *,
    dataset: str,
    series_id: str,
    frequency: str,
    since: pd.Timestamp,
    until: Optional[pd.Timestamp],
) -> pd.Series:
    """Один live-запрос к EIA v2. Бросает RuntimeError на пустых/невалидных результатах.

    EIA v2 пагинирует по 5000 строк max; для 5-летних рядов daily — это <=1825,
    weekly — <=260, monthly — <=60. Запас большой, нам пагинация не нужна.
    """
    import requests

    url = f"{EIA_BASE_URL}/{dataset}/"
    params: dict[str, Any] = {
        "api_key": _api_key(),
        "frequency": frequency,
        "data[0]": "value",
        "facets[series][]": series_id,
        "start": str(since.date()) if hasattr(since, "date") else str(pd.Timestamp(since).date()),
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": 5000,
    }
    if until is not None:
        params["end"] = str(pd.Timestamp(until).date())

    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"EIA HTTP {r.status_code}: {r.text[:200]}")

    payload = r.json()
    response = payload.get("response") or {}
    rows = response.get("data") or []
    if not rows:
        raise RuntimeError(
            f"EIA empty data for {dataset}/{series_id} (frequency={frequency})"
        )

    df = pd.DataFrame(rows)
    if "period" not in df.columns or "value" not in df.columns:
        raise RuntimeError(
            f"EIA unexpected response shape for {dataset}/{series_id}: "
            f"columns={list(df.columns)}"
        )

    # 'value' приходит как строка для некоторых датасетов
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["period"] = pd.to_datetime(df["period"], utc=True)
    df = df.dropna(subset=["value"]).sort_values("period")

    if df.empty:
        raise RuntimeError(f"EIA all-NaN values for {dataset}/{series_id}")

    series = df.set_index("period")["value"].astype(float)
    series.index = series.index.normalize()
    # EIA может вернуть дубликаты по period для weekly-рядов с ревизиями;
    # берём последнее значение per период.
    series = series.groupby(level=0).last()
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


__all__ = [
    "fetch_eia_for_asset",
    "fetch_eia_inventory",
    "EIA_ASSET_ENDPOINTS",
    "EIA_EXOG_ENDPOINTS",
]
