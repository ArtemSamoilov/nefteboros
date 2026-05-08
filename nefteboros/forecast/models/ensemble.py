"""Ensemble forecaster — усреднение SARIMAX + GBR.

CI v2: **mean of component widths centered on ensemble mean** — заменяет
v1 union (min low, max high), который давал чудовищно широкий CI на длинных
horizons (на 12m √h-scaled SARIMAX + GBR conformal residual → ширина >$700
для нефти, что бесполезно). Mean-of-widths сохраняет калибровку approximately
(если компоненты independently calibrated — mean width тоже ~калиброванная)
при ширине ×0.6-0.7 от union.

См. ADR-0012, ADR-0023 §Q1 v3 «CI fix».
"""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd

from nefteboros.forecast.models.base import BaseForecaster
from nefteboros.forecast.schema import (
    ConfidenceInterval,
    ForecastPoint,
    ModelMethod,
)


class EnsembleForecaster(BaseForecaster):
    """Простое усреднение нескольких моделей."""

    method = ModelMethod.ENSEMBLE

    def __init__(self, components: Sequence[BaseForecaster]) -> None:
        super().__init__()
        if len(components) < 2:
            raise ValueError(f"Ensemble требует >= 2 компонентов, got {len(components)}")
        self.components: list[BaseForecaster] = list(components)

    def _fit_impl(
        self,
        history: pd.Series,
        exog: Optional[pd.DataFrame],
    ) -> None:
        for comp in self.components:
            comp.fit(history, exog)

    def _predict_impl(
        self,
        *,
        horizon_months: int,
        levels: tuple[float, ...],
        future_exog: Optional[pd.DataFrame],
    ) -> list[ForecastPoint]:
        comp_predictions = [
            comp.predict(horizon_months, levels=levels, future_exog=future_exog)
            for comp in self.components
        ]
        end_points = [pred[-1] for pred in comp_predictions]
        target_date = end_points[0].date

        mean_value = sum(p.value for p in end_points) / len(end_points)

        # CI v2: mean of component widths, centered on ensemble mean.
        # Это narrower чем union (min low, max high) и даёт sensible CI на
        # длинных horizons. Калибровка приблизительно сохраняется при condition
        # что компоненты independently calibrated.
        ci_80 = self._averaged_ci(end_points, level=0.80, center=mean_value)
        ci_95 = self._averaged_ci(end_points, level=0.95, center=mean_value)

        return [
            ForecastPoint(date=target_date, value=mean_value, ci_80=ci_80, ci_95=ci_95)
        ]

    @staticmethod
    def _averaged_ci(
        end_points: list[ForecastPoint],
        *,
        level: float,
        center: float,
    ) -> ConfidenceInterval:
        """Mean of component half-widths, centered on ensemble mean.

        Каждый компонент даёт свой (low, high) для уровня level. Берём
        среднее half-width = ((high - low) / 2 averaged across components),
        строим CI = [center - half_width_avg, center + half_width_avg].

        Это narrower чем union, но не теряет calibration: если каждый
        компонент имеет ~80% empirical coverage, mean width на ~80% покрывает
        true value около ensemble mean.
        """
        half_widths = []
        for p in end_points:
            ci = p.ci_80 if level == 0.80 else p.ci_95
            half_widths.append((ci.high - ci.low) / 2)
        avg_half_width = sum(half_widths) / len(half_widths)
        return ConfidenceInterval(
            level=level,
            low=center - avg_half_width,
            high=center + avg_half_width,
        )


__all__ = ["EnsembleForecaster"]
