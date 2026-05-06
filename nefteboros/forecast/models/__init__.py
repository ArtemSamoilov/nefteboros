"""Реализации моделей прогноза.

Модули:
  base.py        — BaseForecaster (абстрактный класс с единым API)
  random_walk.py — End-of-month random walk (honest baseline)
  sarimax.py     — SARIMAX с экзогенами (Box-Jenkins, ручной grid по AIC)
  xgboost_m.py   — XGBoost на лагах + macro фичи (CI через split-conformal)
  ensemble.py    — Простое усреднение SARIMAX+XGBoost (или RW+SARIMAX для monthly)

Каждая модель реализует:
    fit(history: pd.Series, exog: Optional[pd.DataFrame]) -> Self
    predict(horizon: Horizon, *, levels=(0.80, 0.95)) -> list[ForecastPoint]

Все модели возвращают аналитический CI там, где это возможно; для XGBoost —
conformal через `nefteboros.forecast.conformal`.

См. ADR-0012 §«Методы прогноза».
"""

from __future__ import annotations
