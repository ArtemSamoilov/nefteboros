"""Источники цен для расчётного модуля.

Модули:
  yf.py        — yfinance (Brent, WTI, HH, TTF, DXY)
  eia.py       — EIA REST API (spot prices, weekly inventories, monthly STEO refs)
  investing.py — investing.com скрейпер (Urals, ESPO, JKM)
  spimex.py    — СПбМТСБ скрейпер (российский индекс газа)
  cbr.py       — ЦБ РФ — экспортные цены газа РФ (xls)
  opec.py      — OPEC reference basket (XML feed) — P1
  exog.py      — экзогенные ряды для SARIMAX (DXY, EIA inventories, futures curve)

Контракт всех fetcher'ов:

    def fetch_<source>(asset_id: str, *, since: pd.Timestamp,
                       use_cache: bool = True) -> pd.Series

Возвращают `pd.Series` с DatetimeIndex (UTC, нормализованный к началу дня),
именем актива и значениями типа float64. Кеш — через `nefteboros.forecast.cache`.

См. ADR-0012 §«Источники данных».
"""

from __future__ import annotations
