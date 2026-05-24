"""High-level forecast API: единая точка входа для агента и CLI.

    forecast(asset, horizon, *, scenario=None) -> ForecastResult | ForecastRefusal

Архитектура (ADR-0024): regime-conditioned mean-reverting Ornstein-Uhlenbeck
per scenario. Заменяет post-modeling shift подход из ADR-0023.

Логика:
  1. Валидация horizon: parse string → Horizon | Refusal | ValueError.
  2. Парсинг scenario (None | "base"|"bear"|"bull" | ScenarioParams).
  3. Lookup asset в registry, проверка is_scenario_applicable.
  4. Fetch spot (последняя observed price) для anchor OU.
  5. Get OU params (μ, θ, σ, infl) для (asset, scenario) из scenarios.py.
  6. compute_ou_forecast(spot, params, horizon_months) → mid + CI 80/95.
  7. Build ForecastResult + interpretation.

Stat-models (RandomWalk/SARIMAX/GBR/Ensemble в models/) сохранены для backtest
infrastructure (ADR-0012), но НЕ используются в production forecast path.
В backtest они нужны для regression testing скоростно/точности OU.

См. ADR-0024 — полная карта решений и калибровка.
"""

from __future__ import annotations

import logging
import math
import random as _stdlib_random
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Optional, Union

import numpy as np
import pandas as pd

from nefteboros.forecast.data.eia import fetch_eia_for_asset
from nefteboros.forecast.data.moex import fetch_moex
from nefteboros.forecast.data.yf import fetch_yfinance
from nefteboros.forecast.interpret import generate_interpretation
from nefteboros.forecast.registry import get_asset
from nefteboros.forecast.scenarios import (
    AS_OF_DATE,
    FORECAST_RANDOM_STATE,
    OIL_ASSETS,
    OUForecast,
    ScenarioParams,
    compute_ou_forecast,
    get_ou_params,
    is_scenario_applicable,
    ou_params_with_flag_mu,
    parse_scenario,
    scenario_label,
    supply_balance_from_flags,
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


# Production method tag: ModelMethod.OU_REGIME (ADR-0024 §«Implementation»).
# Citation format: [Forecast: ou_regime, scenario=<name>, CI <level>].
_OU_METHOD_TAG = ModelMethod.OU_REGIME.value


# =============================================================================
# Public API
# =============================================================================


def forecast(
    asset: str,
    horizon: Union[str, Horizon],
    *,
    scenario: Optional[Union[str, ScenarioParams]] = None,
    flag_states: Optional[Mapping[str, str]] = None,
    history_years: float = 5.0,
    use_cache: bool = True,
    method: Optional[Union[str, ModelMethod]] = None,  # backward-compat, ignored in OU path
) -> Union[ForecastResult, ForecastRefusal]:
    """Прогноз цены актива с OU-based scenario forecast.

    Args:
        asset: один из ASSET_PARAMS (brent, wti, urals, espo, urals_minfin_blend,
               henry_hub, ttf, moexog, gazp, nvtk).
        horizon: "1m" / "3m" / "6m" / "12m". >= 18m → ForecastRefusal.
        scenario: None | "base" | "bear" | "bull" | ScenarioParams. None == "base".
        flag_states: опциональная карта {driver: state} геополитических флагов
               (ADR-0025). None (default) → замороженные μ из ASSET_PARAMS
               (поведение НЕ меняется). Задан → μ пересчитывается детерминированной
               цепочкой DRIVERS → Σ Δmbpd → Kilian; θ/σ остаются из scenario.
               ТОЛЬКО для нефти; для газа/equity → ForecastRefusal.
        history_years: окно для fetch spot (последняя точка). Default 5y.
        use_cache: использовать локальный кеш данных.
        method: backward-compat parameter (ignored — OU не имеет alternative methods).

    Returns:
        ForecastResult с centroidом, CI 80/95, interpretation, metadata.
        ForecastRefusal если asset не applicable или horizon вне области.
    """
    # 0. Reproducibility seed (см. ADR-0024 §A3, scenarios.FORECAST_RANDOM_STATE)
    _seed_for_reproducibility()

    # 1. Validate horizon
    horizon_parsed = _parse_horizon(asset, horizon)
    if isinstance(horizon_parsed, ForecastRefusal):
        return horizon_parsed
    h: Horizon = horizon_parsed

    # 2. Parse scenario
    scenario_params = parse_scenario(scenario)

    # 3. Validate asset has OU calibration
    if not is_scenario_applicable(asset):
        return ForecastRefusal(
            asset=asset,
            requested_horizon_months=h.months,
            reason=(
                f"Asset {asset!r} не имеет OU calibration в ADR-0024 (v2.1). "
                f"Поддерживаются: brent, wti, urals, espo, urals_minfin_blend, "
                f"henry_hub, ttf, moexog, gazp, nvtk. "
                f"opec_basket — fetcher не реализован (P1 backlog)."
            ),
        )

    # 3b. flag_states (ADR-0025) — детерминированная цепочка μ ТОЛЬКО для нефти
    if flag_states is not None and asset not in OIL_ASSETS:
        return ForecastRefusal(
            asset=asset,
            requested_horizon_months=h.months,
            reason=(
                f"flag_states (геополитическая цепочка μ, ADR-0025) поддерживается "
                f"только для нефти {sorted(OIL_ASSETS)}; got {asset!r}. Газ/equity "
                f"сохраняют ручную калибровку — используй scenario= без flag_states."
            ),
        )

    # 4. Validate asset registry
    meta = get_asset(asset)

    logger.info(
        "forecast: asset=%s horizon=%s scenario=%s (OU regime)",
        asset, h.value, scenario_label(scenario_params),
    )

    # 5. Fetch spot (last observation) — используется как S_0 для OU
    spot, history = _fetch_spot_and_history(asset, meta, history_years, use_cache)

    # 6. Get OU params and compute forecast.
    #    flag_states пересчитывает μ через детерминированную цепочку (ADR-0025);
    #    θ/σ/infl остаются из scenario-пресета. flag_states=None (default) →
    #    замороженные μ (snapshot AS_OF_DATE) — поведение forecast() НЕ меняется.
    if flag_states is None:
        ou_params = get_ou_params(asset, scenario_params.name)
    else:
        ou_params = ou_params_with_flag_mu(asset, scenario_params.name, flag_states)
    clip_negative = meta.unit in ("USD/bbl", "USD/MMBtu", "EUR/MWh", "RUB", "pts (RUB-weighted)")
    ou_result = compute_ou_forecast(
        spot=spot,
        params=ou_params,
        horizon_months=h.months,
        clip_negative=clip_negative,
    )

    # 7. Build ForecastResult
    target_date = _compute_target_date(history.index[-1], h.months)
    point = ForecastPoint(
        date=target_date,
        value=ou_result.mid,
        ci_80=ConfidenceInterval(
            level=0.80,
            low=ou_result.ci_80_low,
            high=ou_result.ci_80_high,
        ),
        ci_95=ConfidenceInterval(
            level=0.95,
            low=ou_result.ci_95_low,
            high=ou_result.ci_95_high,
        ),
    )

    result = ForecastResult(
        asset=asset,
        horizon=h,
        method=ModelMethod.OU_REGIME,  # ADR-0024: production OU per scenario
        points=[point],
        interpretation="",  # заполним ниже
        backtest_summary=None,
        metadata={
            "method_tag": _OU_METHOD_TAG,  # legacy tag kept for backward compat
            "primary_source": meta.primary_source.value,
            "spot": spot,
            "spot_observation_date": str(history.index[-1].date()),
            "data_n_points": len(history),
            "data_first_observation": str(history.index.min().date()),
            "data_last_observation": str(history.index.max().date()),
            # OU params (для diagnostic)
            "scenario_label": scenario_label(scenario_params),
            "scenario_params": scenario_params.model_dump(),
            "scenario_applicable": True,
            "scenario_as_of": str(AS_OF_DATE),
            "ou_mu_0": ou_params.mu_0,
            "ou_mu_t": ou_result.mu_t,
            "ou_theta": ou_params.theta,
            "ou_sigma": ou_params.sigma,
            "ou_inflation": ou_params.inflation,
            "ou_raw_anchor": ou_result.raw_anchor,
            # flag-driven μ chain diagnostics (ADR-0025); None если флаги не заданы
            "flag_states": dict(flag_states) if flag_states is not None else None,
            "flag_supply_balance_mbpd": (
                supply_balance_from_flags(flag_states)
                if flag_states is not None
                else None
            ),
        },
    )

    # 8. Interpretation
    return result.model_copy(update={
        "interpretation": generate_interpretation(result),
    })


# =============================================================================
# Internal helpers
# =============================================================================


def _seed_for_reproducibility() -> None:
    """Re-seed numpy и random для детерминированности.

    OU model deterministic by construction; но 3rd-party libs (yfinance, etc.)
    могут иметь implicit random. Защита.
    """
    np.random.seed(FORECAST_RANDOM_STATE)
    _stdlib_random.seed(FORECAST_RANDOM_STATE)


def _fetch_spot_and_history(
    asset: str,
    meta,
    history_years: float,
    use_cache: bool,
) -> tuple[float, pd.Series]:
    """Fetch история для actasset, return (spot, history).

    Для derived активов (urals/espo/blend) в OU-режиме мы НЕ используем
    derived layer — каждый актив имеет свой OU calibration в ASSET_PARAMS.
    Spot для derived подсчитывается через Brent + spread_schedule (proxy).
    """
    since = pd.Timestamp.now(tz="UTC").normalize() - pd.DateOffset(
        years=int(math.ceil(history_years))
    )

    if meta.primary_source == DataSource.DERIVED:
        # Для urals/espo/blend — fetch Brent history, применяем spread_schedule
        # для spot proxy.
        return _fetch_derived_spot(asset, since, use_cache)

    # Observable: yfinance / EIA / MOEX
    return _fetch_observable_spot(asset, meta, since, use_cache)


def _fetch_observable_spot(
    asset: str,
    meta,
    since: pd.Timestamp,
    use_cache: bool,
) -> tuple[float, pd.Series]:
    src = meta.primary_source
    if src == DataSource.YFINANCE:
        history = fetch_yfinance(asset, since=since, use_cache=use_cache)
    elif src == DataSource.EIA:
        history = fetch_eia_for_asset(asset, since=since, use_cache=use_cache)
    elif src == DataSource.MOEX_ISS:
        history = fetch_moex(asset, since=since, use_cache=use_cache)
    else:
        raise ValueError(
            f"asset {asset!r} primary_source={src.value!r} unsupported"
        )

    if history.empty:
        raise RuntimeError(f"forecast: пустая история для {asset!r}")

    spot = float(history.iloc[-1])
    return spot, history


def _fetch_derived_spot(
    asset: str,
    since: pd.Timestamp,
    use_cache: bool,
) -> tuple[float, pd.Series]:
    """Spot для derived активов — Brent − scheduled spread на сегодня."""
    from nefteboros.forecast.data.spread_schedule import get_spread_for_date

    brent_history = fetch_yfinance("brent", since=since, use_cache=use_cache)
    if brent_history.empty:
        raise RuntimeError(f"forecast: пустая Brent история для derived {asset!r}")

    last_date = brent_history.index[-1]
    brent_spot = float(brent_history.iloc[-1])

    if asset == "urals":
        _, mid_discount, _ = get_spread_for_date(last_date, "urals")
        spot = brent_spot - mid_discount
    elif asset == "espo":
        _, mid_discount, _ = get_spread_for_date(last_date, "espo")
        spot = brent_spot - mid_discount
    elif asset == "urals_minfin_blend":
        _, urals_d, _ = get_spread_for_date(last_date, "urals")
        _, espo_d, _ = get_spread_for_date(last_date, "espo")
        urals_spot = brent_spot - urals_d
        espo_spot = brent_spot - espo_d
        spot = 0.78 * urals_spot + 0.22 * espo_spot
    else:
        raise ValueError(f"unknown derived asset: {asset!r}")

    # Pseudo history с одним spot для интерфейса
    pseudo_history = pd.Series(
        [spot],
        index=[last_date],
        name=asset,
    )
    return spot, pseudo_history


def _compute_target_date(last_obs: pd.Timestamp, horizon_months: int) -> datetime:
    """Целевая дата = last_obs + horizon_months calendar months."""
    target = pd.Timestamp(last_obs) + pd.DateOffset(months=horizon_months)
    return target.to_pydatetime()


# =============================================================================
# Horizon parsing (как в v2.0.0)
# =============================================================================


_HORIZON_RE = re.compile(r"^(\d+)\s*([dwmy])$", re.IGNORECASE)


def _parse_horizon(
    asset: str,
    raw: Union[str, Horizon],
) -> Union[Horizon, ForecastRefusal]:
    """Парсинг "1m" / "3m" / "6m" / "12m" / "1y" / etc."""
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

    if unit == "d":
        raise ValueError(
            "Сутки/недели — не наша область (день-трейдинг). Используй >= 1m."
        )
    if unit == "w":
        raise ValueError("Weekly horizons не поддерживаются.")
    if unit == "y":
        n_months = n * 12
    else:
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
            f"horizon {n_months}m не поддерживается. Используй 1m/3m/6m/12m."
        )

    return Horizon(f"{n_months}m")


# =============================================================================
# Backward-compat helpers (used by spread.py)
# =============================================================================


def _fetch_history(
    asset: str,
    *,
    since: pd.Timestamp,
    use_cache: bool,
) -> pd.Series:
    """Backward-compat helper — used by spread.py for series_diff fitting."""
    meta = get_asset(asset)
    src = meta.primary_source

    if src == DataSource.YFINANCE:
        return fetch_yfinance(asset, since=since, use_cache=use_cache)
    if src == DataSource.EIA:
        return fetch_eia_for_asset(asset, since=since, use_cache=use_cache)
    if src == DataSource.MOEX_ISS:
        return fetch_moex(asset, since=since, use_cache=use_cache)
    raise ValueError(
        f"asset {asset!r} primary_source={src.value!r} unsupported in fetcher"
    )


__all__ = ["forecast"]
