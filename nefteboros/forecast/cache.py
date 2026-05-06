"""TTL CSV-кеш для price-fetcher'ов.

Назначение:
  - Не дёргать живые API при каждом forecast()-вызове (rate-limits, latency).
  - Дать graceful fallback, когда live-источник упал.

Layout:
  datasets/forecast_cache/
    <cache_key>.csv     — колонки [date, value], DatetimeIndex
    _meta.json          — {"<cache_key>": {"last_fetched_iso": "..."}}

API:
  read_cache(key)            -> Optional[pd.Series]
  write_cache(key, series)
  is_fresh(key, ttl_hours)   -> bool

Cache-key — обычно asset_id (для primary), либо `exog__<name>` (для экзогенов).
Каждый fetcher решает сам, что использовать.

См. ADR-0012 §«Конфигурация» — env vars FORECAST_CACHE_DIR / FORECAST_CACHE_TTL_HOURS.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


_DEFAULT_CACHE_DIR = Path(
    os.environ.get("FORECAST_CACHE_DIR", "datasets/forecast_cache")
)
_DEFAULT_TTL_HOURS = int(os.environ.get("FORECAST_CACHE_TTL_HOURS", "24"))
_META_FILE = "_meta.json"


def _cache_dir(override: Optional[Path] = None) -> Path:
    return override if override is not None else _DEFAULT_CACHE_DIR


def _csv_path(key: str, *, cache_dir: Optional[Path] = None) -> Path:
    return _cache_dir(cache_dir) / f"{key}.csv"


def _meta_path(*, cache_dir: Optional[Path] = None) -> Path:
    return _cache_dir(cache_dir) / _META_FILE


def _read_meta(*, cache_dir: Optional[Path] = None) -> dict:
    p = _meta_path(cache_dir=cache_dir)
    if not p.exists():
        return {}
    try:
        with p.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("cache: corrupt _meta.json (%s) — treating as empty", e)
        return {}


def _write_meta(meta: dict, *, cache_dir: Optional[Path] = None) -> None:
    p = _meta_path(cache_dir=cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    tmp.replace(p)  # атомарная подмена


# =============================================================================
# Public API
# =============================================================================


def read_cache(
    key: str,
    *,
    cache_dir: Optional[Path] = None,
) -> Optional[pd.Series]:
    """Прочитать закешированный ряд. None если файл не существует/пустой/битый."""
    p = _csv_path(key, cache_dir=cache_dir)
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, parse_dates=["date"])
    except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError) as e:
        logger.warning("cache: failed to read %s: %s", p, e)
        return None

    if df.empty or "date" not in df.columns or "value" not in df.columns:
        return None

    s = df.set_index("date")["value"].astype(float)
    if s.index.tz is None:
        s.index = pd.to_datetime(s.index, utc=True)
    else:
        s.index = s.index.tz_convert("UTC")
    s.name = key
    s = s.sort_index()
    return s


def write_cache(
    key: str,
    series: pd.Series,
    *,
    cache_dir: Optional[Path] = None,
) -> None:
    """Записать ряд и обновить timestamp в _meta.json."""
    if series is None or series.empty:
        logger.warning("cache: refusing to write empty series for %s", key)
        return

    base = _cache_dir(cache_dir)
    base.mkdir(parents=True, exist_ok=True)

    df = series.reset_index()
    df.columns = ["date", "value"]
    p = _csv_path(key, cache_dir=cache_dir)
    df.to_csv(p, index=False)

    meta = _read_meta(cache_dir=cache_dir)
    meta[key] = {
        "last_fetched_iso": datetime.now(timezone.utc).isoformat(),
        "n_points": int(len(series)),
        "first_date": str(series.index.min()),
        "last_date": str(series.index.max()),
    }
    _write_meta(meta, cache_dir=cache_dir)


def is_fresh(
    key: str,
    *,
    ttl_hours: int = _DEFAULT_TTL_HOURS,
    cache_dir: Optional[Path] = None,
) -> bool:
    """True, если запись существует и не старше ttl_hours."""
    meta = _read_meta(cache_dir=cache_dir)
    entry = meta.get(key)
    if not entry or "last_fetched_iso" not in entry:
        return False
    try:
        last = datetime.fromisoformat(entry["last_fetched_iso"])
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last) < timedelta(hours=ttl_hours)


def cache_age_hours(
    key: str,
    *,
    cache_dir: Optional[Path] = None,
) -> Optional[float]:
    """Возраст кеша в часах. None если запись не найдена."""
    meta = _read_meta(cache_dir=cache_dir)
    entry = meta.get(key)
    if not entry or "last_fetched_iso" not in entry:
        return None
    try:
        last = datetime.fromisoformat(entry["last_fetched_iso"])
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 3600.0


__all__ = [
    "read_cache",
    "write_cache",
    "is_fresh",
    "cache_age_hours",
]
