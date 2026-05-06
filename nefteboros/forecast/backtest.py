"""Walk-forward бектест с rolling origin + regime segmentation.

Метрики (см. ADR-0012):
  - MAPE                  — точечная точность (mean abs % error)
  - RMSE                  — точечная точность (root mean squared error)
  - coverage_80 / 95      — эмпирическое покрытие CI на out-of-sample
  - MASE vs RW            — главный критерий: бьёт ли модель persistence
  - directional_accuracy  — доля верно угаданных направлений Δ за horizon

Per-regime сегментация: каждый out-of-sample target_date находится в одном из
4 режимов (`spread_schedule.SPREAD_SCHEDULE`). Метрики агрегируются отдельно по
каждому режиму + общая (`AGGREGATE`).

Walk-forward с rolling origin (НЕ expanding):
  - Train window — фиксированной длины (default 3y).
  - Шаг 1 месяц.
  - На каждом шаге: train на window → predict horizon → compare с actual.

Run-cache: результаты пишутся в JSON (metrics/runs/<...>.json) — для reuse в
эксперименте без повторного бектеста.

См. ADR-0012 §«Бектест».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from nefteboros.forecast.data.spread_schedule import find_period_for_date
from nefteboros.forecast.models.base import BaseForecaster
from nefteboros.forecast.models.random_walk import RandomWalkForecaster
from nefteboros.forecast.schema import (
    BacktestMetrics,
    BacktestRegime,
    BacktestSummary,
    Horizon,
    ModelMethod,
)

logger = logging.getLogger(__name__)


# Маппинг spread_schedule период → BacktestRegime
_PERIOD_TO_REGIME = {
    "pre_war": BacktestRegime.PRE_2022,
    "war_shock": BacktestRegime.RUSSIA_WAR_SHOCK,
    "cap_phase_1": BacktestRegime.CAP_NORMALIZATION,
    "cap_phase_2": BacktestRegime.IRAN_2026,
}


# =============================================================================
# Per-step accumulator
# =============================================================================


@dataclass
class _StepResult:
    target_date: pd.Timestamp
    actual: float
    last_observed: float          # цена в момент train_end — для directional accuracy
    pred: float
    ci_80_low: float
    ci_80_high: float
    ci_95_low: float
    ci_95_high: float
    rw_pred: float                # predicted by RW на тот же target_date
    regime: BacktestRegime


# =============================================================================
# Public API
# =============================================================================


def run_backtest(
    *,
    history: pd.Series,
    model_factory,                 # callable: () -> BaseForecaster (свежий instance per fit)
    method: ModelMethod,
    asset: str,
    horizon: Horizon,
    train_window_years: float = 3.0,
    step_months: int = 1,
    log_transform: bool = False,
) -> BacktestSummary:
    """Walk-forward бектест с rolling origin.

    Args:
        history: pd.Series с DatetimeIndex (UTC), без NaN.
        model_factory: callable, возвращающий новый instance модели per fit
                      (model_factory() — `RandomWalkForecaster()`).
        method: ModelMethod значение для записи в результат.
        asset: ID актива (для регистрации в результате).
        horizon: целевой horizon прогноза.
        train_window_years: длина rolling-train окна.
        step_months: шаг origin в месяцах.
        log_transform: применять log(price) перед обучением и exp() обратно.

    Returns:
        BacktestSummary с per-regime + aggregate метриками.
    """
    if history.empty:
        raise ValueError("history is empty")
    if history.isna().any():
        raise ValueError("history contains NaN")

    h_days = horizon.trading_days
    train_window_days = int(train_window_years * 252)
    step_days = int(step_months * 21)  # 21 trading day per month

    n = len(history)
    if n < train_window_days + h_days:
        raise ValueError(
            f"history too short: n={n}, need >= {train_window_days + h_days}"
        )

    # Подготовка: log-transform если нужен
    if log_transform and (history > 0).all():
        series_for_model = np.log(history)
        is_log = True
    else:
        series_for_model = history
        is_log = False

    history_arr = history.to_numpy()
    series_arr = series_for_model.to_numpy()

    # RW baseline: для каждого target_t, prediction = history[t - h_days] (persistence)
    # Считаем сразу для всех точек, выбираем подмножество на которых будем оценивать.

    results: list[_StepResult] = []

    # Rolling origin: t — конец train окна, target = t + h_days
    t = train_window_days
    while t + h_days < n:
        train_slice = slice(max(0, t - train_window_days), t)
        train = pd.Series(
            series_arr[train_slice],
            index=history.index[train_slice],
        )

        target_idx = t + h_days
        target_date = history.index[target_idx]
        actual = float(history_arr[target_idx])
        last_obs = float(history_arr[t - 1])

        # Fit model
        try:
            model = model_factory()
            model.fit(train)
            pts = model.predict(horizon.months, levels=(0.80, 0.95))
            p = pts[-1]
            pred = float(p.value)
            ci80_low, ci80_high = p.ci_80.low, p.ci_80.high
            ci95_low, ci95_high = p.ci_95.low, p.ci_95.high
        except Exception as e:
            logger.warning(
                "backtest: %s @ t=%d (target=%s) failed: %s",
                method.value, t, target_date.date(), e,
            )
            t += step_days
            continue

        # Inverse log
        if is_log:
            pred = float(np.exp(pred))
            ci80_low = float(np.exp(ci80_low))
            ci80_high = float(np.exp(ci80_high))
            ci95_low = float(np.exp(ci95_low))
            ci95_high = float(np.exp(ci95_high))

        # RW pred: persistence = history[t-1]
        rw_pred = last_obs

        # Determine regime via spread_schedule period
        try:
            period = find_period_for_date(target_date)
            regime = _PERIOD_TO_REGIME.get(period.name, BacktestRegime.AGGREGATE)
        except ValueError:
            regime = BacktestRegime.AGGREGATE

        results.append(_StepResult(
            target_date=target_date,
            actual=actual,
            last_observed=last_obs,
            pred=pred,
            ci_80_low=ci80_low,
            ci_80_high=ci80_high,
            ci_95_low=ci95_low,
            ci_95_high=ci95_high,
            rw_pred=rw_pred,
            regime=regime,
        ))

        t += step_days

    if not results:
        raise RuntimeError(
            f"backtest produced 0 results for {asset}/{method.value}/{horizon.value}"
        )

    # Aggregate metrics per regime
    per_regime: list[BacktestMetrics] = []
    by_regime: dict[BacktestRegime, list[_StepResult]] = {}
    for r in results:
        by_regime.setdefault(r.regime, []).append(r)

    for regime, group in by_regime.items():
        per_regime.append(_compute_metrics(group, regime))

    # Aggregate across regimes
    per_regime.append(_compute_metrics(results, BacktestRegime.AGGREGATE))

    return BacktestSummary(
        asset=asset,
        horizon=horizon,
        method=method,
        train_window_years=train_window_years,
        history_window_years=(history.index.max() - history.index.min()).days / 365.25,
        rolling_step_months=step_months,
        per_regime=per_regime,
    )


# =============================================================================
# Metric computation
# =============================================================================


def _compute_metrics(
    group: list[_StepResult],
    regime: BacktestRegime,
) -> BacktestMetrics:
    n = len(group)
    if n == 0:
        return BacktestMetrics(regime=regime, n_forecasts=0)

    actuals = np.array([r.actual for r in group])
    preds = np.array([r.pred for r in group])
    rw_preds = np.array([r.rw_pred for r in group])
    last_obs = np.array([r.last_observed for r in group])
    ci80_low = np.array([r.ci_80_low for r in group])
    ci80_high = np.array([r.ci_80_high for r in group])
    ci95_low = np.array([r.ci_95_low for r in group])
    ci95_high = np.array([r.ci_95_high for r in group])

    # MAPE (skipping zeros)
    safe = np.abs(actuals) > 1e-9
    mape = float(np.mean(np.abs((preds[safe] - actuals[safe]) / actuals[safe]))) * 100.0 if safe.any() else None

    # RMSE
    rmse = float(np.sqrt(np.mean((preds - actuals) ** 2)))

    # Coverage
    cov_80 = float(np.mean((actuals >= ci80_low) & (actuals <= ci80_high)))
    cov_95 = float(np.mean((actuals >= ci95_low) & (actuals <= ci95_high)))

    # MASE vs RW
    mae_model = float(np.mean(np.abs(preds - actuals)))
    mae_rw = float(np.mean(np.abs(rw_preds - actuals)))
    mase = (mae_model / mae_rw) if mae_rw > 1e-9 else None

    # Directional accuracy
    actual_direction = np.sign(actuals - last_obs)
    pred_direction = np.sign(preds - last_obs)
    # Strict: учитываем zero-direction как match
    matches = (actual_direction == pred_direction)
    dir_acc = float(np.mean(matches))

    return BacktestMetrics(
        regime=regime,
        n_forecasts=n,
        mape=mape,
        rmse=rmse,
        coverage_80=cov_80,
        coverage_95=cov_95,
        mase_vs_rw=mase,
        directional_accuracy=dir_acc,
    )


__all__ = ["run_backtest"]
