"""End-of-month random walk — honest baseline.

Прогноз = последнее наблюдаемое значение (persistence). CI — empirical residual:
квантили исторических `Δ_h` где `Δ_h(t) = price(t+h) - price(t)`. Это даёт
distribution-free CI без предположений о nonormalности — что важно для нефти/газа
с heavy tails и шоками.

Литература (Alquist-Kilian 2010, Empirical Economics 2024) показывает что
end-of-month RW бьёт большинство сложных моделей на 1-12m horizons. Без него
в наборе любая ML/SARIMAX выглядит «лучше прошлого» иллюзорно. См. ADR-0012.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

from nefteboros.forecast.models.base import BaseForecaster
from nefteboros.forecast.schema import ConfidenceInterval, ForecastPoint, ModelMethod


class RandomWalkForecaster(BaseForecaster):
    """Persistence forecast с empirical-residual CI.

    Args:
        residual_window_years: какой длины исторические Δ_h использовать для CI.
                              5 лет покрывают разные режимы и не overfit на свежий.
    """

    method = ModelMethod.RANDOM_WALK

    def __init__(self, residual_window_years: float = 5.0) -> None:
        super().__init__()
        self.residual_window_years = residual_window_years
        self._last_value: Optional[float] = None
        self._last_date: Optional[pd.Timestamp] = None

    # ---------- BaseForecaster impl ----------

    def _fit_impl(
        self,
        history: pd.Series,
        exog: Optional[pd.DataFrame],
    ) -> None:
        self._last_value = float(history.iloc[-1])
        self._last_date = history.index[-1]

    def _predict_impl(
        self,
        *,
        horizon_months: int,
        levels: tuple[float, ...],
        future_exog: Optional[pd.DataFrame],
    ) -> list[ForecastPoint]:
        history = self._history
        assert history is not None
        h_days = self._horizon_to_trading_days(horizon_months)

        # Empirical Δ_h на rolling-окне residual_window_years
        cutoff = self._last_date - pd.Timedelta(days=int(self.residual_window_years * 365.25))
        window = history[history.index >= cutoff]
        # Δ(t) = price(t + h_days) − price(t) — в индексных позициях это shift на h_days назад
        deltas = window - window.shift(h_days)
        deltas = deltas.dropna()

        if len(deltas) < 30:
            # окно слишком мало; используем gaussian fallback с σ из daily-returns
            daily_returns = window.diff().dropna()
            sigma_daily = float(daily_returns.std(ddof=1))
            # σ_h-day ≈ σ_daily × sqrt(h)
            sigma_h = sigma_daily * np.sqrt(h_days)
            target_date = self._generate_target_dates(horizon_months)[0]
            cis = {lvl: self._build_ci(self._last_value, sigma_h, lvl) for lvl in levels}
            return [
                ForecastPoint(
                    date=target_date,
                    value=self._last_value,
                    ci_80=cis.get(0.80, self._build_ci(self._last_value, sigma_h, 0.80)),
                    ci_95=cis.get(0.95, self._build_ci(self._last_value, sigma_h, 0.95)),
                )
            ]

        # Empirical CI: квантили distribution Δ
        target_date = self._generate_target_dates(horizon_months)[0]
        deltas_arr = deltas.to_numpy()

        ci_pairs: dict[float, ConfidenceInterval] = {}
        for level in levels:
            alpha = 1.0 - level
            q_low = float(np.quantile(deltas_arr, alpha / 2))
            q_high = float(np.quantile(deltas_arr, 1.0 - alpha / 2))
            ci_pairs[level] = ConfidenceInterval(
                level=level,
                low=self._last_value + q_low,   # шире чем nominal: q_low отрицательный
                high=self._last_value + q_high,
            )

        # Гарантируем что 0.80 и 0.95 присутствуют (иначе schema validation упадёт)
        ci_80 = ci_pairs.get(0.80) or self._empirical_ci(deltas_arr, 0.80)
        ci_95 = ci_pairs.get(0.95) or self._empirical_ci(deltas_arr, 0.95)

        return [
            ForecastPoint(
                date=target_date,
                value=self._last_value,
                ci_80=ci_80,
                ci_95=ci_95,
            )
        ]

    def _empirical_ci(self, deltas: np.ndarray, level: float) -> ConfidenceInterval:
        alpha = 1.0 - level
        return ConfidenceInterval(
            level=level,
            low=self._last_value + float(np.quantile(deltas, alpha / 2)),
            high=self._last_value + float(np.quantile(deltas, 1.0 - alpha / 2)),
        )


__all__ = ["RandomWalkForecaster"]
