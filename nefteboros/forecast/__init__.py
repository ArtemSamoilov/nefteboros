"""Расчётный модуль прогнозирования цен Brent.

Будет содержать:
  - data.py       — загрузка истории через yfinance / EIA API / CSV
  - arima.py      — ARIMA/SARIMA модель с автовыбором порядка
  - prophet_m.py  — Facebook Prophet с holidays и changepoints
  - ets.py        — экспоненциальное сглаживание (baseline)
  - backtest.py   — rolling-window валидация, MAPE/RMSE/coverage
  - interpret.py  — генерация текстовой интерпретации прогноза

См. docs/adr/0004-arima-prophet.md (TBD).
"""
