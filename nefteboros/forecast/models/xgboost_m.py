"""Gradient Boosting forecaster — лагах + опциональные macro-фичи + conformal CI.

В PR1 — `sklearn.GradientBoostingRegressor` (no libomp dependency).
В PR3 — переход на XGBoost (`xgboost.XGBRegressor`) для +5-10% метрик при
доступности libomp в окружении. Имя метода в `ModelMethod` остаётся `xgboost`
для consistency публичного API.

См. ADR-0012 §«Методы прогноза».
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from nefteboros.forecast.conformal import (
    split_conformal_intervals,
    time_series_split,
)
from nefteboros.forecast.models.base import BaseForecaster
from nefteboros.forecast.schema import ConfidenceInterval, ForecastPoint, ModelMethod

logger = logging.getLogger(__name__)


class XGBoostForecaster(BaseForecaster):
    """GBR (sklearn в PR1) с recursive multi-step prediction + split-conformal CI."""

    method = ModelMethod.XGBOOST

    def __init__(
        self,
        *,
        n_lags: int = 21,
        proper_train_frac: float = 0.80,
        n_estimators: int = 200,
        max_depth: int = 4,
        learning_rate: float = 0.05,
        random_state: int = 42,
    ) -> None:
        super().__init__()
        self.n_lags = n_lags
        self.proper_train_frac = proper_train_frac
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.random_state = random_state

        self._model = None
        self._calibration_residuals: dict[int, np.ndarray] = {}
        self._exog_cols: list[str] = []

    def _fit_impl(
        self,
        history: pd.Series,
        exog: Optional[pd.DataFrame],
    ) -> None:
        from sklearn.ensemble import GradientBoostingRegressor

        if len(history) < self.n_lags * 3:
            raise ValueError(
                f"history too short for n_lags={self.n_lags}: need >= {self.n_lags * 3}, "
                f"got {len(history)}"
            )

        X, y = self._build_features(history, exog)
        n = len(X)

        train_slice, calib_slice = time_series_split(n, proper_train_frac=self.proper_train_frac)

        X_train, y_train = X[train_slice], y[train_slice]

        self._model = GradientBoostingRegressor(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            random_state=self.random_state,
            loss="squared_error",
        )
        self._model.fit(X_train, y_train)

        # 1-step calibration residuals
        self._calibration_residuals = {}
        X_calib, y_calib = X[calib_slice], y[calib_slice]
        if len(X_calib) >= 10:
            preds_1step = self._model.predict(X_calib)
            self._calibration_residuals[1] = np.abs(y_calib - preds_1step)

    def _predict_impl(
        self,
        *,
        horizon_months: int,
        levels: tuple[float, ...],
        future_exog: Optional[pd.DataFrame],
    ) -> list[ForecastPoint]:
        assert self._model is not None
        assert self._history is not None
        h_days = self._horizon_to_trading_days(horizon_months)

        history_arr = self._history.to_numpy()
        last_lags = list(history_arr[-self.n_lags:])

        if self._exog is not None:
            if future_exog is not None and len(future_exog) >= h_days:
                future_exog_arr = future_exog.iloc[:h_days][self._exog_cols].values
            else:
                last_exog = self._exog[self._exog_cols].iloc[-1].values
                future_exog_arr = np.tile(last_exog, (h_days, 1))
        else:
            future_exog_arr = None

        predictions = []
        current_lags = last_lags.copy()
        for step in range(h_days):
            features = list(current_lags)
            if future_exog_arr is not None:
                features.extend(future_exog_arr[step])
            x = np.array([features])
            y_hat = float(self._model.predict(x)[0])
            predictions.append(y_hat)
            current_lags = current_lags[1:] + [y_hat]

        end_value = predictions[-1]

        # Conformal CI: 1-step residuals scaled √h
        if 1 in self._calibration_residuals:
            base_resids = self._calibration_residuals[1]
            scaled_resids = base_resids * np.sqrt(h_days)
        else:
            sigma = float(self._history.diff().std() * np.sqrt(h_days))
            scaled_resids = np.array([sigma * 1.282])

        ci_dict = split_conformal_intervals(scaled_resids, point=end_value, levels=levels)
        ci_80 = ci_dict.get(0.80) or split_conformal_intervals(
            scaled_resids, point=end_value, levels=(0.80,)
        )[0.80]
        ci_95 = ci_dict.get(0.95) or split_conformal_intervals(
            scaled_resids, point=end_value, levels=(0.95,)
        )[0.95]

        target_date = self._generate_target_dates(horizon_months)[0]
        return [
            ForecastPoint(date=target_date, value=end_value, ci_80=ci_80, ci_95=ci_95)
        ]

    def _build_features(
        self,
        history: pd.Series,
        exog: Optional[pd.DataFrame],
    ) -> tuple[np.ndarray, np.ndarray]:
        prices = history.to_numpy()
        n_total = len(prices)
        n_samples = n_total - self.n_lags

        X_lags = np.zeros((n_samples, self.n_lags))
        for i in range(n_samples):
            X_lags[i] = prices[i : i + self.n_lags]
        y = prices[self.n_lags:]

        if exog is not None:
            self._exog_cols = list(exog.columns)
            exog_aligned = exog.iloc[self.n_lags:].values
            X = np.concatenate([X_lags, exog_aligned], axis=1)
        else:
            self._exog_cols = []
            X = X_lags

        return X, y


__all__ = ["XGBoostForecaster"]
