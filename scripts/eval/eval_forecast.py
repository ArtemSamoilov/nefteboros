"""Eval forecast: ARIMA vs Prophet vs ETS vs naive baselines.

PLACEHOLDER. Реальная реализация — в PR `feature/forecast`.

Датасет: datasets/forecast_history.csv (история Brent с yfinance)

Метрики:
  MAPE       — Mean Absolute Percentage Error (основная)
  RMSE, MAE  — для сравнения моделей в абсолютных $/баррель
  coverage   — доля точек, попавших в CI 80% и 95% (calibration)

Бектест: rolling window, горизонты 1m/3m/6m.

См. docs/experiments/design.md §4.
"""


def main() -> int:
    raise NotImplementedError("eval_forecast — заглушка")


if __name__ == "__main__":
    raise SystemExit(main())
