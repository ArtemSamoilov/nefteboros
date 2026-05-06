"""Split-conformal prediction wrapper для нестатистических моделей (XGBoost/GBR).

Идея:
  1. Данные делятся: proper-train | calibration.
  2. Модель учится на proper-train.
  3. На calibration вычисляются abs residuals.
  4. Квантиль (n+1)(1-α)/n от |residual| даёт `ε`.
  5. CI на новый прогноз = [ŷ − ε, ŷ + ε].

При нестабильных режимах calibration window должен быть rolling и достаточно
свежий, чтобы охватить актуальный режим волатильности. Если структурный слом
случился позднее последней calibration-window — coverage будет недо-номинальный
(модель оптимистична). Это видно в бектесте per regime — это сигнал, не баг.

Литература: Xu & Xie 2020 (CP for time series), Zaffran 2022 (adaptive CP).

См. ADR-0012 §«Доверительные интервалы».
"""

from __future__ import annotations

import numpy as np

from nefteboros.forecast.schema import ConfidenceInterval


def split_conformal_intervals(
    abs_residuals: np.ndarray,
    *,
    point: float,
    levels: tuple[float, ...] = (0.80, 0.95),
) -> dict[float, ConfidenceInterval]:
    """ε и CI для каждого level из массива abs(residuals)."""
    if abs_residuals.size < 10:
        raise ValueError(
            f"too few calibration residuals ({abs_residuals.size}); need >= 10"
        )

    out: dict[float, ConfidenceInterval] = {}
    for level in levels:
        n = len(abs_residuals)
        q_level = min(1.0, np.ceil((n + 1) * level) / n)
        eps = float(np.quantile(abs_residuals, q_level, method="linear"))
        out[level] = ConfidenceInterval(
            level=level, low=point - eps, high=point + eps,
        )
    return out


def time_series_split(
    n: int,
    *,
    proper_train_frac: float = 0.80,
) -> tuple[slice, slice]:
    """Разделить ряд на (proper_train_idx, calibration_idx) без shuffle."""
    if not (0.5 <= proper_train_frac < 1.0):
        raise ValueError(f"proper_train_frac must be in [0.5, 1.0), got {proper_train_frac}")
    cut = int(n * proper_train_frac)
    return slice(0, cut), slice(cut, n)


__all__ = ["split_conformal_intervals", "time_series_split"]
