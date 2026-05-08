"""Расчётный модуль прогнозирования цен нефти и газа.

Покрытие активов (10 P0):

  Глобальная нефть:        brent, wti
  Российская нефть:        urals, espo, urals_minfin_blend (derived)
  Глобальный газ:          henry_hub, ttf, jkm
  Российский газ:          spimex_gas (daily), cbr_gas_export (monthly)

Модели прогноза (4):

  random_walk  — honest baseline (end-of-month persistence)
  sarimax      — Box-Jenkins + экзогены (EIA inventories, DXY, futures curve)
  xgboost      — ML на лагах + macro; CI через split-conformal prediction
  ensemble     — mean(SARIMAX, XGBoost) с bootstrap CI

Горизонты: 1m / 3m / 6m / 12m. >=18m возвращает отказ + перенаправление на
сценарные прогнозы из RAG-корпуса (WOO 2025, IEA Oil 2025, ИНЭИ).

Бектест: walk-forward с rolling origin, окно истории 5 лет, шаг 1 мес.
Метрики: MAPE / RMSE / Coverage(80/95) / **MASE relative to RW** / directional accuracy.
Регимы: pre_2022 / russia_war_shock / cap_normalization / iran_2026 — отдельные
метрики для каждого + aggregate.

Структура:

  api.py        — высокоуровневый forecast(asset, horizon)
  schema.py     — pydantic-контракты (ForecastResult, BacktestMetrics, ...)
  registry.py   — реестр активов с метаданными
  cache.py      — CSV-кеш с TTL для fetcher'ов
  data/         — источники (yfinance, EIA, investing.com, SPIMEX, CBR, exog)
  models/       — реализации моделей (base + 4 конкретные)
  conformal.py  — split-conformal prediction wrapper
  backtest.py   — walk-forward + regime segmentation
  interpret.py  — horizon-aware текстовая интерпретация результата

Архитектура и обоснования: docs/adr/0012-price-tools.md
Эксперименты с метриками: docs/experiments/forecast.md
"""

from __future__ import annotations

__all__ = [
    "forecast",
    "forecast_spread",
    "Horizon",
    "AssetID",
    "ForecastResult",
    "SpreadForecastResult",
    "SpreadScenarioEntry",
    "ConfidenceInterval",
    "BacktestMetrics",
    "ScenarioParams",
    "PRESET_SCENARIOS",
]


def __getattr__(name: str):
    # Lazy-импорты, чтобы тяжёлые зависимости (statsmodels, xgboost, prophet*)
    # не грузились при `import nefteboros.forecast` без необходимости.
    # *Prophet НЕ используем — оставлено как пример паттерна.
    if name == "forecast":
        from nefteboros.forecast.api import forecast as _forecast
        return _forecast
    if name == "forecast_spread":
        from nefteboros.forecast.spread import forecast_spread as _forecast_spread
        return _forecast_spread
    if name in (
        "Horizon", "AssetID",
        "ForecastResult", "SpreadForecastResult", "SpreadScenarioEntry",
        "ConfidenceInterval", "BacktestMetrics",
    ):
        from nefteboros.forecast import schema as _schema
        return getattr(_schema, name)
    if name in ("ScenarioParams", "PRESET_SCENARIOS"):
        from nefteboros.forecast import scenarios as _scenarios
        return getattr(_scenarios, name)
    raise AttributeError(f"module 'nefteboros.forecast' has no attribute {name!r}")
