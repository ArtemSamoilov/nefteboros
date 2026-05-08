"""High-level forecast API: единая точка входа для агента и CLI.

    forecast(asset, horizon, *, scenario=None, method=None) -> ForecastResult | ForecastRefusal

Логика:
  1. Валидация horizon: parse string → Horizon | Refusal | ValueError.
  2. Lookup asset в registry.
  3. Парсинг scenario (None | "base"|"bear"|"bull" | ScenarioParams).
  4. Если asset = derived (urals/espo/blend) → рекурсивно прогнозируем base
     (Brent) **с тем же scenario**, применяем derived layer.
  5. Иначе:
     - fetch history через подходящий fetcher (yfinance/eia/moex).
     - применяем log-transform если meta.log_transform.
     - выбираем method (явный override или per-horizon default).
     - fit → predict.
     - откатываем log-transform.
     - **применяем post-modeling shift:** base anchor (observed_spot - raw_model)
       + scenario delta (per Q1 ADR-0023).
     - строим interpretation.
     - возвращаем ForecastResult с scenario metadata.

Сценарный режим (ADR-0023):
  - base = current shock state, anchored to spot
  - bear = de-escalation (Hormuz reopens, Iran partial lift)
  - bull = escalation (Hormuz fully closed)

См. ADR-0012 §«Горизонты прогноза», ADR-0023 §«Решения».
"""

from __future__ import annotations

import logging
import math
import re
from typing import Optional, Union

import numpy as np
import pandas as pd

from nefteboros.forecast.data.eia import fetch_eia_for_asset
from nefteboros.forecast.data.moex import fetch_moex
from nefteboros.forecast.data.yf import fetch_yfinance
from nefteboros.forecast.derived_layer import (
    derive_espo_forecast,
    derive_minfin_blend_forecast,
    derive_urals_forecast,
)
from nefteboros.forecast.interpret import generate_interpretation
from nefteboros.forecast.models.base import BaseForecaster
from nefteboros.forecast.models.ensemble import EnsembleForecaster
from nefteboros.forecast.models.random_walk import RandomWalkForecaster
from nefteboros.forecast.models.sarimax import SARIMAXForecaster
from nefteboros.forecast.models.xgboost_m import XGBoostForecaster
from nefteboros.forecast.registry import get_asset
from nefteboros.forecast.scenarios import (
    AS_OF_DATE,
    FORECAST_RANDOM_STATE,
    BaseAnchor,
    ScenarioDelta,
    ScenarioParams,
    compute_base_anchor,
    compute_scenario_delta,
    is_scenario_applicable,
    parse_scenario,
    scenario_label,
)
from nefteboros.forecast.schema import (
    ConfidenceInterval,
    DataSource,
    ForecastPoint,
    ForecastRefusal,
    ForecastResult,
    Horizon,
    ModelMethod,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Default method выбор per (asset, horizon)
# Будет уточнён по результатам бектеста в docs/experiments/forecast.md.
# Пока — sensible defaults на основе literature (Baumeister-Kilian, MDPI 2025).
# =============================================================================

_DEFAULT_METHOD_BY_HORIZON = {
    Horizon.M1: ModelMethod.RANDOM_WALK,    # на 1m RW почти всегда лучший
    Horizon.M3: ModelMethod.ENSEMBLE,
    Horizon.M6: ModelMethod.ENSEMBLE,
    Horizon.M12: ModelMethod.ENSEMBLE,
}


# =============================================================================
# Public API
# =============================================================================


def forecast(
    asset: str,
    horizon: Union[str, Horizon],
    *,
    scenario: Optional[Union[str, ScenarioParams]] = None,
    method: Optional[Union[str, ModelMethod]] = None,
    history_years: float = 5.0,
    use_cache: bool = True,
) -> Union[ForecastResult, ForecastRefusal]:
    """Прогноз цены актива на горизонт.

    Args:
        asset: один из ASSET_REGISTRY (brent, wti, urals, ...).
        horizon: "1m" / "3m" / "6m" / "12m". Поддержка "1d"/"1w" — отсутствует
                 (область дей-трейдинга). >= 18m → ForecastRefusal.
        scenario: None | "base" | "bear" | "bull" | ScenarioParams. None == "base".
                  В v2.1 сценарии применяются к brent/wti/urals/espo/blend; для
                  других активов (газ, RU proxy) scenario ignored с warning.
                  См. ADR-0023.
        method: переопределение модели. Если None — default per horizon.
        history_years: сколько лет истории брать для обучения.
        use_cache: использовать ли локальный кеш данных.

    Returns:
        ForecastResult с centroidом, CI 80/95, interpretation, metadata.
        metadata содержит scenario_label, base_anchor_shift, scenario_delta_*.
        ForecastRefusal если horizon вне области точечных прогнозов.

    Raises:
        ValueError: невалидный asset, horizon-формат или scenario name.
        RuntimeError: данные недоступны и кеш пуст.
    """
    # 0. Reproducibility: re-seed numpy/random в начале каждого вызова
    # (см. ADR-0023 §A3, scenarios.FORECAST_RANDOM_STATE).
    _seed_for_reproducibility()

    # 1. Validate horizon
    horizon_parsed = _parse_horizon(asset, horizon)
    if isinstance(horizon_parsed, ForecastRefusal):
        return horizon_parsed
    h: Horizon = horizon_parsed

    # 2. Validate asset
    meta = get_asset(asset)

    # 3. Parse scenario
    scenario_params = parse_scenario(scenario)

    # 4. Validate method
    if method is None:
        chosen_method = _default_method_for(asset, h)
    else:
        chosen_method = method if isinstance(method, ModelMethod) else ModelMethod(method)
    if chosen_method not in meta.available_methods:
        raise ValueError(
            f"asset={asset!r} не поддерживает method={chosen_method.value!r}. "
            f"Доступны: {[m.value for m in meta.available_methods]}"
        )

    logger.info(
        "forecast: asset=%s horizon=%s method=%s scenario=%s",
        asset, h.value, chosen_method.value, scenario_label(scenario_params),
    )

    # 5. Routing: derived vs observable
    if meta.primary_source == DataSource.DERIVED:
        return _forecast_derived(
            asset, h, chosen_method, scenario_params, history_years, use_cache,
        )

    return _forecast_observable(
        asset, h, chosen_method, scenario_params, history_years, use_cache,
    )


# =============================================================================
# Internal — observable path
# =============================================================================


def _forecast_observable(
    asset: str,
    horizon: Horizon,
    method: ModelMethod,
    scenario_params: ScenarioParams,
    history_years: float,
    use_cache: bool,
) -> ForecastResult:
    meta = get_asset(asset)

    since = pd.Timestamp.now(tz="UTC").normalize() - pd.DateOffset(
        years=int(math.ceil(history_years))
    )
    history = _fetch_history(asset, since=since, use_cache=use_cache)

    if history.empty or len(history) < 30:
        raise RuntimeError(
            f"forecast: недостаточно данных для {asset!r} "
            f"(got n={len(history)}, need >=30)"
        )

    # Apply log-transform (для газовых рядов с экстремумами)
    # `(history > 0).all()` returns ``numpy.bool_`` (pandas Series.all()), not
    # Python ``bool``. Python ``and`` returns the second operand as-is, so
    # ``use_log`` stays ``numpy.bool_``. Pydantic v2 + numpy 2.x cannot
    # serialize ``numpy.bool_`` in ``model_dump(mode="json")``, which crashes
    # the synthesize node downstream. ``bool(...)`` coerces to native Python.
    use_log = bool(meta.log_transform and (history > 0).all())
    if use_log:
        history_for_model = np.log(history)
        history_for_model.name = history.name
    else:
        history_for_model = history

    # Build & fit model
    model = _build_model(method)
    model.fit(history_for_model)

    # Predict (raw — model belief без scenario shift)
    raw_points = model.predict(horizon.months, levels=(0.80, 0.95))

    # Inverse log-transform if used
    if use_log:
        raw_points = [_exp_point(p) for p in raw_points]

    # Apply scenario shift: base anchor (horizon-decayed) + scenario delta
    # (horizon-scaled). См. ADR-0023 §Q1 v3. Для assets вне scenario
    # applicability — shift = 0, warning в interpretation.
    observed_spot = float(history.iloc[-1])
    raw_target_value = raw_points[-1].value
    anchor = compute_base_anchor(
        raw_model_value=raw_target_value,
        observed_spot=observed_spot,
        horizon_months=horizon.months,
        scenario_name=scenario_label(scenario_params),
    )

    # Refusal: 12m base в shock-режиме (см. ADR-0023 §Q1 v3 «refusal на 12m»).
    # Persistent shock 12 месяцев — historically unlikely (Hormuz 2026, war 2022,
    # COVID 2020 — все < 12 месяцев). Точечная оценка бесполезна.
    raw_shift = anchor.observed_spot - anchor.raw_model_value
    shock_ratio = abs(raw_shift) / max(abs(anchor.observed_spot), 1.0)
    if (
        is_scenario_applicable(asset)
        and horizon.months == 12
        and scenario_params == parse_scenario("base")
        and shock_ratio > 0.15
    ):
        return ForecastRefusal(
            asset=asset,
            requested_horizon_months=12,
            reason=(
                f"Точечный прогноз {asset!r} на 12m в base scenario неустойчив: "
                f"market в shock-режиме (anchor shift {raw_shift:+.1f} = "
                f"{shock_ratio*100:.0f}% от spot {observed_spot:.1f}). "
                f"Persistent shock 12 месяцев исторически unlikely (Hormuz 2026, "
                f"war 2022, COVID 2020 — все < 12m). Используй scenario='bear' "
                f"(de-escalation) или 'bull' (escalation), либо обратись к "
                f"сценарным прогнозам в RAG-корпусе (WOO 2025, IEA Oil 2025)."
            ),
        )

    if is_scenario_applicable(asset):
        delta = compute_scenario_delta(
            scenario_params, asset, horizon_months=horizon.months,
        )
    else:
        delta = ScenarioDelta(low=0.0, mid=0.0, high=0.0, driver_breakdown={})

    # Price-positive активы — clip CI к [0, +∞) (negative price невозможен)
    clip_negative = meta.unit in ("USD/bbl", "USD/MMBtu", "EUR/MWh")
    shifted_points = [
        _apply_scenario_shift(p, anchor, delta, clip_negative=clip_negative)
        for p in raw_points
    ]

    # Build result
    result = ForecastResult(
        asset=asset,
        horizon=horizon,
        method=method,
        points=shifted_points,
        interpretation="",  # заполним ниже
        backtest_summary=None,  # заполняется бектестом отдельно (eval-скрипт)
        metadata=_build_metadata(
            meta=meta,
            history=history,
            use_log=use_log,
            history_years=history_years,
            anchor=anchor,
            delta=delta,
            scenario_params=scenario_params,
            scenario_applicable=is_scenario_applicable(asset),
            raw_target_value=raw_target_value,
        ),
    )
    # Interpretation
    result_with_text = result.model_copy(update={
        "interpretation": generate_interpretation(result),
    })
    return result_with_text


def _apply_scenario_shift(
    point: ForecastPoint,
    anchor: BaseAnchor,
    delta: ScenarioDelta,
    *,
    clip_negative: bool = False,
) -> ForecastPoint:
    """Применить anchor + scenario delta к одной точке прогноза.

    Total shift = anchor + delta. Anchor — horizon-decayed contribution current
    state (1m × 1.0 → 12m × 0.15). Delta — scenario shift (0 для base,
    horizon-scaled для bear/bull). CI расширяется на calibration uncertainty
    диапазона delta.

    Args:
        point: исходная raw model точка (без shift).
        anchor: BaseAnchor с уже применённым horizon decay.
        delta: ScenarioDelta с уже применённым horizon scaling.
        clip_negative: если True — clip ci_low к 0 (для price-positive активов).
                       Negative spot oil/gas физически невозможен; CI шириной
                       включающей negative — артефакт ensemble union scaled √h.
    """
    a = anchor.anchor_shift
    new_value = point.value + a + delta.mid
    ci_80_low = point.ci_80.low + a + delta.low
    ci_80_high = point.ci_80.high + a + delta.high
    ci_95_low = point.ci_95.low + a + delta.low
    ci_95_high = point.ci_95.high + a + delta.high

    if clip_negative:
        ci_80_low = max(0.0, ci_80_low)
        ci_95_low = max(0.0, ci_95_low)
        new_value = max(0.0, new_value)

    return ForecastPoint(
        date=point.date,
        value=new_value,
        ci_80=ConfidenceInterval(level=0.80, low=ci_80_low, high=ci_80_high),
        ci_95=ConfidenceInterval(level=0.95, low=ci_95_low, high=ci_95_high),
    )


def _build_metadata(
    *,
    meta,
    history: pd.Series,
    use_log: bool,
    history_years: float,
    anchor: BaseAnchor,
    delta: ScenarioDelta,
    scenario_params: ScenarioParams,
    scenario_applicable: bool,
    raw_target_value: float,
) -> dict:
    """Сформировать metadata-блок для ForecastResult.

    Включает базовые data-fields + scenario-блок (для diagnostic в Langfuse и
    для interpret.py).
    """
    return {
        "primary_source": meta.primary_source.value,
        "data_n_points": len(history),
        "data_first_observation": str(history.index.min().date()),
        "data_last_observation": str(history.index.max().date()),
        "data_last_value": float(history.iloc[-1]),
        "log_transform_applied": use_log,
        "history_years_requested": history_years,
        # Scenario block (ADR-0023)
        "scenario_label": scenario_label(scenario_params),
        "scenario_params": scenario_params.model_dump(),
        "scenario_applicable": scenario_applicable,
        "scenario_as_of": str(AS_OF_DATE),
        "raw_model_target_value": raw_target_value,
        "base_anchor_shift": anchor.anchor_shift,
        "scenario_delta_low": delta.low,
        "scenario_delta_mid": delta.mid,
        "scenario_delta_high": delta.high,
        "scenario_driver_breakdown": delta.driver_breakdown,
    }


def _exp_point(p: ForecastPoint) -> ForecastPoint:
    """Inverse log-transform для одной точки. CI границы тоже exp()."""
    return ForecastPoint(
        date=p.date,
        value=float(np.exp(p.value)),
        ci_80=ConfidenceInterval(
            level=0.80,
            low=float(np.exp(p.ci_80.low)),
            high=float(np.exp(p.ci_80.high)),
        ),
        ci_95=ConfidenceInterval(
            level=0.95,
            low=float(np.exp(p.ci_95.low)),
            high=float(np.exp(p.ci_95.high)),
        ),
    )


# =============================================================================
# Internal — derived path
# =============================================================================


def _forecast_derived(
    asset: str,
    horizon: Horizon,
    method: ModelMethod,
    scenario_params: ScenarioParams,
    history_years: float,
    use_cache: bool,
) -> ForecastResult:
    """Derived assets — scenario applies через base Brent forecast.

    derived_layer.py принимает ForecastResult и накладывает spread (Urals/ESPO)
    либо комбинирует (blend). Если base Brent уже содержит scenario shift —
    derived автоматически наследует.
    """
    if asset == "urals":
        brent_fc = _forecast_observable(
            "brent", horizon, method, scenario_params, history_years, use_cache,
        )
        if isinstance(brent_fc, ForecastRefusal):
            return _propagate_refusal_to_derived(brent_fc, asset)
        result = derive_urals_forecast(brent_fc)
        result = _clip_derived_ci(result)
        return _attach_interpretation(result)

    if asset == "espo":
        brent_fc = _forecast_observable(
            "brent", horizon, method, scenario_params, history_years, use_cache,
        )
        if isinstance(brent_fc, ForecastRefusal):
            return _propagate_refusal_to_derived(brent_fc, asset)
        result = derive_espo_forecast(brent_fc)
        result = _clip_derived_ci(result)
        return _attach_interpretation(result)

    if asset == "urals_minfin_blend":
        brent_fc = _forecast_observable(
            "brent", horizon, method, scenario_params, history_years, use_cache,
        )
        if isinstance(brent_fc, ForecastRefusal):
            return _propagate_refusal_to_derived(brent_fc, asset)
        urals_fc = derive_urals_forecast(brent_fc)
        espo_fc = derive_espo_forecast(brent_fc)
        result = derive_minfin_blend_forecast(urals_fc, espo_fc)
        result = _clip_derived_ci(result)
        return _attach_interpretation(result)

    raise ValueError(f"unknown derived asset: {asset!r}")


def _propagate_refusal_to_derived(
    brent_refusal: ForecastRefusal,
    derived_asset: str,
) -> ForecastRefusal:
    """Brent refusal → derived asset refusal с обновлённым asset/reason."""
    return ForecastRefusal(
        asset=derived_asset,
        requested_horizon_months=brent_refusal.requested_horizon_months,
        reason=(
            f"Прогноз {derived_asset!r} получается из Brent через derived layer; "
            f"Brent отказался: {brent_refusal.reason}"
        ),
    )


def _clip_derived_ci(result: ForecastResult) -> ForecastResult:
    """Clip CI к 0 для derived price-positive активов.

    Derived layer вычитает spread из Brent CI, что может создать negative low.
    Negative spot oil невозможен — domain-clip.
    """
    new_points: list[ForecastPoint] = []
    for p in result.points:
        new_points.append(ForecastPoint(
            date=p.date,
            value=max(0.0, p.value),
            ci_80=ConfidenceInterval(
                level=0.80,
                low=max(0.0, p.ci_80.low),
                high=p.ci_80.high,
            ),
            ci_95=ConfidenceInterval(
                level=0.95,
                low=max(0.0, p.ci_95.low),
                high=p.ci_95.high,
            ),
        ))
    return result.model_copy(update={"points": new_points})


def _attach_interpretation(result: ForecastResult) -> ForecastResult:
    return result.model_copy(update={
        "interpretation": generate_interpretation(result),
    })


# =============================================================================
# Internal — helpers
# =============================================================================


def _seed_for_reproducibility() -> None:
    """Re-seed numpy и random для детерминированности (см. ADR-0023 §A3).

    Применяется в начале forecast() и forecast_spread() — защита от 3rd-party
    implicit-random (statsmodels SARIMAX optimizer init, scipy.optimize)
    при последовательных вызовах в одной сессии.
    """
    import random as _random

    np.random.seed(FORECAST_RANDOM_STATE)
    _random.seed(FORECAST_RANDOM_STATE)


_HORIZON_RE = re.compile(r"^(\d+)\s*([dwmy])$", re.IGNORECASE)


def _parse_horizon(
    asset: str,
    raw: Union[str, Horizon],
) -> Union[Horizon, ForecastRefusal]:
    """Парсинг "1m" / "3m" / "6m" / "12m" / "18m" / "1y" / etc.

    1d/1w → ValueError (область дей-трейдинга, не наша).
    >= 18m → ForecastRefusal.
    """
    if isinstance(raw, Horizon):
        return raw

    if not isinstance(raw, str):
        raise TypeError(f"horizon must be str or Horizon, got {type(raw).__name__}")

    s = raw.strip().lower()
    m = _HORIZON_RE.match(s)
    if not m:
        raise ValueError(
            f"invalid horizon format: {raw!r}. Use '1m'/'3m'/'6m'/'12m'."
        )

    n = int(m.group(1))
    unit = m.group(2)

    # Convert to months
    if unit == "d":
        raise ValueError(
            "Сутки/недели — не наша область (это дей-трейдинг). "
            "Используй >= 1m. Если нужен 1d — RW + futures почти всегда лучшие, "
            "стат-модели не дают добавленной ценности."
        )
    if unit == "w":
        raise ValueError("Weekly horizons не поддерживаются (см. сообщение про '1d').")
    if unit == "y":
        n_months = n * 12
    else:  # 'm'
        n_months = n

    if n_months >= 18:
        return ForecastRefusal(
            asset=asset,
            requested_horizon_months=n_months,
            reason=(
                f"Точечный прогноз на {n_months} месяцев бесполезен — "
                "литература (Baumeister-Kilian, EIA STEO) показывает что "
                "стат-модели проигрывают сценарным подходам на горизонтах "
                ">=18m. Используй сценарные источники в RAG-корпусе."
            ),
        )

    if n_months not in {1, 3, 6, 12}:
        raise ValueError(
            f"horizon {n_months}m не поддерживается. "
            f"Используй один из 1m/3m/6m/12m."
        )

    return Horizon(f"{n_months}m")


def _default_method_for(asset: str, horizon: Horizon) -> ModelMethod:
    """Default method per horizon. Будет уточнено по бектесту в эксперименте."""
    return _DEFAULT_METHOD_BY_HORIZON[horizon]


def _build_model(method: ModelMethod) -> BaseForecaster:
    if method == ModelMethod.RANDOM_WALK:
        return RandomWalkForecaster()
    if method == ModelMethod.SARIMAX:
        return SARIMAXForecaster()
    if method == ModelMethod.XGBOOST:
        return XGBoostForecaster()
    if method == ModelMethod.ENSEMBLE:
        return EnsembleForecaster([
            SARIMAXForecaster(),
            XGBoostForecaster(),
        ])
    raise ValueError(f"unsupported method: {method}")


def _fetch_history(
    asset: str,
    *,
    since: pd.Timestamp,
    use_cache: bool,
) -> pd.Series:
    meta = get_asset(asset)
    src = meta.primary_source

    if src == DataSource.YFINANCE:
        return fetch_yfinance(asset, since=since, use_cache=use_cache)
    if src == DataSource.EIA:
        return fetch_eia_for_asset(asset, since=since, use_cache=use_cache)
    if src == DataSource.MOEX_ISS:
        return fetch_moex(asset, since=since, use_cache=use_cache)
    raise ValueError(
        f"asset {asset!r} primary_source={src.value!r} — unsupported in this fetcher router"
    )


__all__ = ["forecast"]
