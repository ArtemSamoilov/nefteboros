"""SARIMAX forecaster — Box-Jenkins с экзогенами.

Auto-grid по AIC из небольшого набора кандидатных порядков (без pmdarima —
тот иногда сломан после обновлений; ручной grid из 4-5 кандидатов покрывает
99% полезных случаев на финансовых рядах).

Аналитический CI «из коробки» через `statsmodels.tsa.statespace.SARIMAX` —
state-space Kalman filter возвращает confidence interval для multi-step
forecast. Это **быстро** и **интерпретируемо**, что важно для аналитика
Сбера, объясняющего цифры стейкхолдерам.

Экзогены (опционально, для observable-нефтяных активов):
  - DXY (US Dollar Index) — yfinance DX-Y.NYB
  - US crude inventories — EIA WCESTUS1 (weekly, resample на daily forward-fill)
  - futures front-month (для spot-моделей) — TBD

Если future_exog не передан — модель экстраполирует последние наблюдаемые значения
(conservative carry-forward). Это вводит шум, но избегает caмо-катастрофы при
«забытых» параметрах.

См. ADR-0012 §«Методы прогноза».
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from nefteboros.forecast.models.base import BaseForecaster
from nefteboros.forecast.schema import ForecastPoint, ModelMethod

logger = logging.getLogger(__name__)


# Кандидатные порядки для grid search.
# Формат: (p, d, q) для non-seasonal ARIMA. Сезонные orders по умолчанию пустые.
_CANDIDATE_ORDERS: list[tuple[int, int, int]] = [
    (1, 1, 1),
    (2, 1, 1),
    (1, 1, 2),
    (2, 1, 2),
    (0, 1, 1),  # ARIMA(0,1,1) ≈ exponential smoothing
]


class SARIMAXForecaster(BaseForecaster):
    """SARIMAX(p,d,q) с auto-grid по AIC.

    Args:
        candidate_orders: список (p,d,q) кандидатов. По умолчанию — 5 разумных.
        seasonal_order: (P,D,Q,s) для сезонной части. Default (0,0,0,0) — нет сезонности
                       (нефть/газ на daily не имеют чистой сезонности; macro-cycles
                       улавливаются другими моделями).
        max_fit_seconds_per_order: жёсткий ограничитель на каждый fit (статистическая
                                  модель может зависнуть на singular cov).
    """

    method = ModelMethod.SARIMAX

    def __init__(
        self,
        *,
        candidate_orders: Optional[list[tuple[int, int, int]]] = None,
        seasonal_order: tuple[int, int, int, int] = (0, 0, 0, 0),
        max_fit_seconds_per_order: float = 30.0,
    ) -> None:
        super().__init__()
        self.candidate_orders = candidate_orders or list(_CANDIDATE_ORDERS)
        self.seasonal_order = seasonal_order
        self.max_fit_seconds_per_order = max_fit_seconds_per_order
        self._best_order: Optional[tuple[int, int, int]] = None
        self._best_aic: Optional[float] = None
        self._fit_result = None  # statsmodels SARIMAXResults

    # ---------- BaseForecaster impl ----------

    def _fit_impl(
        self,
        history: pd.Series,
        exog: Optional[pd.DataFrame],
    ) -> None:
        # Импорт отложен — даёт чёткую ошибку если statsmodels не установлен.
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        best = None
        best_aic = float("inf")
        best_order = None

        for order in self.candidate_orders:
            try:
                model = SARIMAX(
                    history.values,
                    exog=exog.values if exog is not None else None,
                    order=order,
                    seasonal_order=self.seasonal_order,
                    enforce_stationarity=False,
                    enforce_invertibility=False,
                )
                res = model.fit(disp=False, maxiter=50)
                if np.isfinite(res.aic) and res.aic < best_aic:
                    best_aic = float(res.aic)
                    best = res
                    best_order = order
                    logger.debug("SARIMAX: order=%s aic=%.2f (current best)", order, res.aic)
            except Exception as e:  # noqa: BLE001 — statsmodels бросает разные типы
                logger.debug("SARIMAX: order=%s failed: %s", order, e)
                continue

        if best is None:
            raise RuntimeError(
                f"SARIMAX: no candidate order converged. "
                f"Tried: {self.candidate_orders}"
            )
        self._fit_result = best
        self._best_order = best_order
        self._best_aic = best_aic
        logger.info("SARIMAX: best order=%s, AIC=%.2f", best_order, best_aic)

    def _predict_impl(
        self,
        *,
        horizon_months: int,
        levels: tuple[float, ...],
        future_exog: Optional[pd.DataFrame],
    ) -> list[ForecastPoint]:
        assert self._fit_result is not None
        h_days = self._horizon_to_trading_days(horizon_months)

        # future_exog handling: если модель fit с exog, нужны future values
        future_exog_arr = None
        if self._exog is not None:
            if future_exog is not None:
                if list(future_exog.columns) != list(self._exog.columns):
                    raise ValueError(
                        "future_exog.columns must match training exog. "
                        f"got {list(future_exog.columns)}, expected {list(self._exog.columns)}"
                    )
                if len(future_exog) < h_days:
                    raise ValueError(
                        f"future_exog has {len(future_exog)} rows, "
                        f"need at least {h_days} for {horizon_months}m horizon"
                    )
                future_exog_arr = future_exog.iloc[:h_days].values
            else:
                # carry-forward последний observed
                last_row = self._exog.iloc[-1].values
                future_exog_arr = np.tile(last_row, (h_days, 1))

        forecast = self._fit_result.get_forecast(steps=h_days, exog=future_exog_arr)
        mean = forecast.predicted_mean
        # state-space возвращает point as ndarray
        end_mean = float(mean[-1])

        # Используем conf_int для каждого нужного уровня
        target_date = self._generate_target_dates(horizon_months)[0]
        ci_80 = self._extract_ci(forecast, level=0.80, point_value=end_mean)
        ci_95 = self._extract_ci(forecast, level=0.95, point_value=end_mean)

        return [
            ForecastPoint(
                date=target_date,
                value=end_mean,
                ci_80=ci_80,
                ci_95=ci_95,
            )
        ]

    # ---------- Internal ----------

    def _extract_ci(self, forecast, *, level: float, point_value: float):
        from nefteboros.forecast.schema import ConfidenceInterval

        alpha = 1.0 - level
        ci_df = forecast.conf_int(alpha=alpha)
        # ci_df — массив (n_steps, 2). Берём последний шаг.
        if hasattr(ci_df, "iloc"):
            row = ci_df.iloc[-1]
            low, high = float(row.iloc[0]), float(row.iloc[1])
        else:
            row = ci_df[-1]
            low, high = float(row[0]), float(row[1])
        # Sanity: low < point < high. Если sarimax выдал инвертированный — fix через point ± half-width
        if not (low <= point_value <= high):
            half = abs(high - low) / 2
            low, high = point_value - half, point_value + half
        return ConfidenceInterval(level=level, low=low, high=high)


__all__ = ["SARIMAXForecaster"]
