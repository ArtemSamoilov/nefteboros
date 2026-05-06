"""Ensemble forecaster — простое усреднение SARIMAX + XGBoost.

CI: union (min low, max high) — conservative; в эксперименте видно, что часто
достаточно для номинального покрытия.

См. ADR-0012 §«Методы прогноза».
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

        ci_80 = ConfidenceInterval(
            level=0.80,
            low=min(p.ci_80.low for p in end_points),
            high=max(p.ci_80.high for p in end_points),
        )
        ci_95 = ConfidenceInterval(
            level=0.95,
            low=min(p.ci_95.low for p in end_points),
            high=max(p.ci_95.high for p in end_points),
        )

        return [
            ForecastPoint(date=target_date, value=mean_value, ci_80=ci_80, ci_95=ci_95)
        ]


__all__ = ["EnsembleForecaster"]
