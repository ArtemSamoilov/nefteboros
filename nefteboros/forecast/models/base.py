"""Базовый интерфейс для прогнозных моделей.

Все модели в `nefteboros.forecast.models.*` реализуют контракт `BaseForecaster`.
Это даёт:
  - единый pipeline для бектеста (ему всё равно, какая модель)
  - возможность ансамблирования
  - предсказуемость API для будущих моделей (PR3: GARCH-LSTM, MS-AR, ...)

Контракт:

    forecaster.fit(history, exog=None)            # обучение
    forecaster.predict(horizon_months, levels)    # прогноз с CI

`history` — pd.Series с DatetimeIndex (UTC), без NaN.
`exog` — опциональный pd.DataFrame с тем же индексом + колонки фичей.

Возвращаемое — `list[ForecastPoint]`. Минимум — одна точка (на конец горизонта);
модели могут возвращать промежуточные точки для построения trajectory.

См. ADR-0012 §«Методы прогноза».
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

import pandas as pd

from nefteboros.forecast.schema import ConfidenceInterval, ForecastPoint, ModelMethod


# Z-значения для нормального CI
_Z_BY_LEVEL = {0.80: 1.282, 0.90: 1.645, 0.95: 1.960, 0.99: 2.576}


class BaseForecaster(ABC):
    """Абстрактный класс для всех моделей.

    Subclasses обязаны:
      - присвоить self.method (ModelMethod)
      - реализовать _fit_impl() и _predict_impl()

    Жизненный цикл:
      forecaster = ConcreteForecaster(...)
      forecaster.fit(history, exog)        # сохраняет внутреннее состояние
      points = forecaster.predict(3)        # 3 месяца
    """

    method: ModelMethod  # должно быть присвоено в __init__ subclass'а

    def __init__(self) -> None:
        self._fitted: bool = False
        self._history: Optional[pd.Series] = None
        self._exog: Optional[pd.DataFrame] = None

    # ---------- Public API ----------

    def fit(
        self,
        history: pd.Series,
        exog: Optional[pd.DataFrame] = None,
    ) -> "BaseForecaster":
        """Обучить модель на истории.

        Returns self для chaining.
        """
        self._validate_history(history)
        if exog is not None:
            self._validate_exog(history, exog)
        self._history = history
        self._exog = exog
        self._fit_impl(history, exog)
        self._fitted = True
        return self

    def predict(
        self,
        horizon_months: int,
        *,
        levels: Sequence[float] = (0.80, 0.95),
        future_exog: Optional[pd.DataFrame] = None,
    ) -> list[ForecastPoint]:
        """Прогноз на horizon_months месяцев вперёд.

        levels: уровни доверия для CI (например 0.80, 0.95).
        future_exog: future значения экзогенов (если модель их использует).
                     Если None — модель должна сама управлять (auto-extend last
                     observed values, или fail если она сильно зависит от exog).
        """
        if not self._fitted:
            raise RuntimeError(
                f"{self.__class__.__name__}.predict() called before fit(). "
                "Call .fit(history) first."
            )
        if horizon_months <= 0:
            raise ValueError(f"horizon_months must be > 0, got {horizon_months}")
        return self._predict_impl(
            horizon_months=horizon_months,
            levels=tuple(levels),
            future_exog=future_exog,
        )

    # ---------- Subclass hooks ----------

    @abstractmethod
    def _fit_impl(
        self,
        history: pd.Series,
        exog: Optional[pd.DataFrame],
    ) -> None:
        """Сохранить внутренние параметры. Не возвращает значения."""

    @abstractmethod
    def _predict_impl(
        self,
        *,
        horizon_months: int,
        levels: tuple[float, ...],
        future_exog: Optional[pd.DataFrame],
    ) -> list[ForecastPoint]:
        """Сгенерировать список ForecastPoint."""

    # ---------- Helpers for subclasses ----------

    @staticmethod
    def _validate_history(history: pd.Series) -> None:
        if not isinstance(history, pd.Series):
            raise TypeError(f"history must be pd.Series, got {type(history)}")
        if not isinstance(history.index, pd.DatetimeIndex):
            raise TypeError("history.index must be DatetimeIndex")
        if history.empty:
            raise ValueError("history is empty")
        if history.isna().any():
            n_na = int(history.isna().sum())
            raise ValueError(f"history contains {n_na} NaN values; clean upstream")

    @staticmethod
    def _validate_exog(history: pd.Series, exog: pd.DataFrame) -> None:
        if not isinstance(exog, pd.DataFrame):
            raise TypeError(f"exog must be pd.DataFrame, got {type(exog)}")
        if not exog.index.equals(history.index):
            raise ValueError(
                "exog.index must equal history.index — align upstream "
                f"(history n={len(history)}, exog n={len(exog)})"
            )
        if exog.isna().any().any():
            raise ValueError("exog contains NaN; impute upstream")

    @staticmethod
    def _build_ci(mean: float, sigma: float, level: float) -> ConfidenceInterval:
        """Симметричный normal-CI."""
        if level not in _Z_BY_LEVEL:
            raise ValueError(
                f"unsupported CI level={level}. Supported: {sorted(_Z_BY_LEVEL)}"
            )
        z = _Z_BY_LEVEL[level]
        return ConfidenceInterval(level=level, low=mean - z * sigma, high=mean + z * sigma)

    @staticmethod
    def _horizon_to_trading_days(horizon_months: int) -> int:
        """21 trading day на месяц — простая конвенция."""
        return horizon_months * 21

    @staticmethod
    def _next_trading_day(after: pd.Timestamp) -> pd.Timestamp:
        """Грубо — следующий рабочий день (Mon-Fri); holidays не учитываем."""
        d = pd.Timestamp(after) + pd.Timedelta(days=1)
        while d.weekday() >= 5:  # 5 = Sat, 6 = Sun
            d += pd.Timedelta(days=1)
        return d

    def _generate_target_dates(
        self,
        horizon_months: int,
    ) -> list[pd.Timestamp]:
        """Сгенерировать список целевых дат: только конец горизонта.

        Subclass'ы могут override чтобы возвращать промежуточные точки.
        """
        if self._history is None:
            raise RuntimeError("fit() not called")
        last_obs = self._history.index[-1]
        n_days = self._horizon_to_trading_days(horizon_months)
        target = pd.Timestamp(last_obs)
        for _ in range(n_days):
            target = self._next_trading_day(target)
        return [target]


__all__ = ["BaseForecaster"]
