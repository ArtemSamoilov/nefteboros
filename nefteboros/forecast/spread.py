"""Forecast spread tool — OU per scenario для (Brent, Urals) и (Brent, WTI).

Реализация Track A2 из roadmap v2.1; обоснования — ADR-0024.

Spread имеет свои OU параметры per pair per scenario. Mean-reversion
наблюдается на spreads особенно сильно: они bounded structurally — Urals
discount определяется sanction policy framework (cap, shadow fleet logistic
premium); Brent-WTI spread определяется US shale supply geographic isolation.

Calibration:

  (brent, urals):
    bear: μ=$8.5  (de-escalation, cap non-binding, return к pre-war norm)
    base: μ=$17   (cap_phase_2 schedule current)
    bull: μ=$25   (escalation, secondary sanctions tighten, war_shock-like)
    Всё через schedule-based logic; OU dynamics с быстрой reversion (θ=4).

  (brent, wti):
    bear: μ=$3   (compresses к pre-shock norm)
    base: μ=$5   (current model output)
    bull: μ=$8   (US shale isolation premium при ME blocked)
    Mean-reverting natural через arbitrage; OU θ=5 (very fast reversion).

Closed list of pairs: (brent, urals), (brent, wti). Любая другая →
ForecastRefusal.

См. ADR-0024 §«Spread per-scenario».
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from nefteboros.forecast.api import _fetch_history, _parse_horizon, _seed_for_reproducibility
from nefteboros.forecast.data.spread_schedule import (
    find_period_for_date,
    get_spread_for_date,
)
from nefteboros.forecast.scenarios import (
    AS_OF_DATE,
    OUParams,
    ScenarioName,
    compute_ou_forecast,
)
from nefteboros.forecast.schema import (
    ConfidenceInterval,
    ForecastRefusal,
    Horizon,
    SpreadForecastResult,
    SpreadScenarioEntry,
)

logger = logging.getLogger(__name__)


# Closed list of supported pairs
_VALID_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("brent", "urals"),
    ("brent", "wti"),
})


# =============================================================================
# OU parameters per spread pair per scenario
# =============================================================================
# Spread mean-reverts faster чем individual prices (structural arbitrage).
# Inflation drift на spreads ~ 0 (spreads — relative, не absolute).

_SPREAD_OU_PARAMS: dict[tuple[str, str], dict[ScenarioName, OUParams]] = {
    # (brent, urals): schedule-based mean per scenario; OU with fast reversion
    # source: spread_schedule.py 4 режима + per-scenario cap binding logic
    ("brent", "urals"): {
        # bear: cap non-binding, discount narrows к pre-war/early-cap range
        "bear": OUParams(mu_0=8.5, theta=4.0, sigma=0.30, inflation=0.0),
        # base: current cap_phase_2 mid
        "base": OUParams(mu_0=17.0, theta=3.0, sigma=0.20, inflation=0.0),
        # bull: secondary sanctions risk, war_shock 2022 levels
        "bull": OUParams(mu_0=25.0, theta=2.5, sigma=0.25, inflation=0.0),
    },
    # (brent, wti): mean-reverting natural через arbitrage; very fast θ
    # source: pre-shock norm $1-3, current $5, US shale isolation premium при ME shock
    ("brent", "wti"): {
        "bear": OUParams(mu_0=3.0, theta=5.0, sigma=0.40, inflation=0.0),
        "base": OUParams(mu_0=5.0, theta=4.0, sigma=0.45, inflation=0.0),
        "bull": OUParams(mu_0=8.0, theta=3.0, sigma=0.50, inflation=0.0),
    },
}


# =============================================================================
# Public API
# =============================================================================


def forecast_spread(
    asset_a: str,
    asset_b: str,
    horizon: str | Horizon,
    *,
    history_years: float = 5.0,
    use_cache: bool = True,
) -> SpreadForecastResult | ForecastRefusal:
    """Прогноз spread с per-scenario разбивкой через OU framework.

    Возвращает три сценария (bear/base/bull) — creator: «один спред на все
    сценарии ни о чём; spread сам зависит от geopolitics, разный per scenario».

    Args:
        asset_a, asset_b: один из закрытого списка пар (brent, urals) | (brent, wti).
        horizon: "1m" / "3m" / "6m" / "12m".
        history_years: окно для подсчёта current spread.
        use_cache: использовать локальный кеш данных.

    Returns:
        SpreadForecastResult с per_scenario={"bear","base","bull"}, или
        ForecastRefusal если pair вне closed list / horizon вне области.
    """
    _seed_for_reproducibility()

    # 1. Validate pair
    pair = (asset_a, asset_b)
    if pair not in _VALID_PAIRS:
        valid_str = ", ".join(f"({a},{b})" for a, b in sorted(_VALID_PAIRS))
        return ForecastRefusal(
            asset=f"{asset_a}-{asset_b}",
            requested_horizon_months=0,
            reason=(
                f"Pair ({asset_a}, {asset_b}) вне закрытого списка. "
                f"Поддерживаются: {valid_str}. См. ADR-0024 §«Spread per-scenario»."
            ),
        )

    # 2. Validate horizon
    horizon_parsed = _parse_horizon(asset_a, horizon)
    if isinstance(horizon_parsed, ForecastRefusal):
        return horizon_parsed
    h: Horizon = horizon_parsed

    logger.info(
        "forecast_spread: pair=(%s, %s) horizon=%s (OU regime)",
        asset_a, asset_b, h.value,
    )

    # 3. Get current spread (S_0 для OU)
    current_spread, target_date, method_tag = _get_current_spread(
        pair, h, history_years, use_cache,
    )

    # 4. Compute OU forecasts for all 3 scenarios
    per_scenario: dict[str, SpreadScenarioEntry] = {}
    for scenario_name in ("bear", "base", "bull"):
        params = _SPREAD_OU_PARAMS[pair][scenario_name]
        ou = compute_ou_forecast(
            spot=current_spread,
            params=params,
            horizon_months=h.months,
            clip_negative=False,  # spreads могут быть negative (Brent < WTI)
        )
        commentary = _commentary_for_pair(pair, scenario_name, ou.mid, params)
        drivers = _drivers_for_pair(pair, scenario_name)
        per_scenario[scenario_name] = SpreadScenarioEntry(
            scenario=scenario_name,
            spread_value=ou.mid,
            ci_80=ConfidenceInterval(level=0.80, low=ou.ci_80_low, high=ou.ci_80_high),
            ci_95=ConfidenceInterval(level=0.95, low=ou.ci_95_low, high=ou.ci_95_high),
            commentary=commentary,
            drivers=drivers,
        )

    interpretation = _build_interpretation(pair, h, current_spread, per_scenario, method_tag)

    return SpreadForecastResult(
        asset_a=asset_a,
        asset_b=asset_b,
        horizon=h,
        target_date=target_date,
        per_scenario=per_scenario,
        interpretation=interpretation,
        metadata={
            "method_tag": "ou_regime_spread",
            "spread_method": method_tag,
            "current_spread": current_spread,
            "scenario_as_of": str(AS_OF_DATE),
        },
    )


# =============================================================================
# Current spread fetch
# =============================================================================


def _get_current_spread(
    pair: tuple[str, str],
    horizon: Horizon,
    history_years: float,
    use_cache: bool,
) -> tuple[float, "pd.Timestamp", str]:
    """Получить current spread (S_0 для OU), target_date, method tag."""
    a, b = pair
    if pair == ("brent", "urals"):
        # Через spread_schedule (Urals derived)
        target_date = pd.Timestamp.now().normalize() + pd.DateOffset(
            months=horizon.months
        )
        # Current spread = schedule mid в текущей дате
        _, current_mid, _ = get_spread_for_date(pd.Timestamp.now().normalize(), "urals")
        return current_mid, target_date, "schedule_anchored_ou"

    if pair == ("brent", "wti"):
        # Через actual series diff
        since = pd.Timestamp.now(tz="UTC").normalize() - pd.DateOffset(
            years=int(math.ceil(history_years))
        )
        brent_h = _fetch_history("brent", since=since, use_cache=use_cache)
        wti_h = _fetch_history("wti", since=since, use_cache=use_cache)
        common_idx = brent_h.index.intersection(wti_h.index)
        if len(common_idx) < 30:
            raise RuntimeError(
                f"forecast_spread(brent, wti): мало общих точек "
                f"(got n={len(common_idx)}, need >=30)"
            )
        last_diff = brent_h.loc[common_idx].iloc[-1] - wti_h.loc[common_idx].iloc[-1]
        target_date = pd.Timestamp.now().normalize() + pd.DateOffset(
            months=horizon.months
        )
        return float(last_diff), target_date, "series_diff_ou"

    raise RuntimeError(f"unhandled pair: {pair}")


# =============================================================================
# Commentary and drivers
# =============================================================================


def _commentary_for_pair(
    pair: tuple[str, str],
    scenario: str,
    spread_mid: float,
    params: OUParams,
) -> str:
    if pair == ("brent", "urals"):
        return _commentary_brent_urals(scenario, spread_mid, params)
    if pair == ("brent", "wti"):
        return _commentary_brent_wti(scenario, spread_mid, params)
    return ""


def _commentary_brent_urals(scenario: str, spread_mid: float, params: OUParams) -> str:
    if scenario == "base":
        return (
            f"Urals discount **${spread_mid:.1f}/bbl**. Текущий cap_phase_2 режим: "
            f"G7 cap $47.60 / EU dynamic $44.10. Shadow fleet absorbs логистический "
            f"premium. Mean reversion к long-run target $17 (θ={params.theta:g}, "
            f"half-life {math.log(2)/params.theta*12:.1f} мес)."
        )
    if scenario == "bear":
        return (
            f"При de-escalation Urals discount сужается к **${spread_mid:.1f}/bbl**. "
            f"Если spot Brent падает к $75-90 → cap $47.60 становится non-binding "
            f"(spot < cap×ratio threshold), shadow fleet premium снижается. "
            f"Long-run target $8.5 (pre-war norm + post-cap normalization)."
        )
    return (
        f"При escalation Urals discount расширяется к **${spread_mid:.1f}/bbl**. "
        f"Hormuz closure → secondary sanctions risk на shadow fleet операторов "
        f"растёт; китайские/индийские buyers требуют дополнительный discount. "
        f"Long-run target $25 (war_shock 2022 levels)."
    )


def _commentary_brent_wti(scenario: str, spread_mid: float, params: OUParams) -> str:
    half_life = math.log(2) / params.theta * 12
    if scenario == "base":
        return (
            f"Brent-WTI spread **${spread_mid:.2f}/bbl**. Текущее равновесие. "
            f"Mean-reverting natural (arbitrage): θ={params.theta:g}, "
            f"half-life {half_life:.1f} мес. Long-run target $5."
        )
    if scenario == "bear":
        return (
            f"При de-escalation spread compresses к **${spread_mid:.2f}/bbl** "
            f"(target $3 — pre-shock norm). Глобальная supply нормализуется, "
            f"US shale perturbation premium уходит."
        )
    return (
        f"При escalation Hormuz blocked → ME oil isolated. US shale получает "
        f"relative supply advantage, WTI lags за Brent. Spread расширяется "
        f"к **${spread_mid:.2f}/bbl** (target $8 — US shale isolation premium)."
    )


def _drivers_for_pair(pair: tuple[str, str], scenario: str) -> list[str]:
    if pair == ("brent", "urals"):
        if scenario == "base":
            return ["russia_cap=active ($47.60 G7 / $44.10 EU)", "shadow_fleet=stable"]
        if scenario == "bear":
            return [
                "hormuz=partial_reopen → spot Brent падает",
                "russia_cap=non-binding (spot < cap threshold)",
                "shadow_fleet_premium=снижается",
            ]
        return [
            "hormuz=partial_closure → secondary sanctions tightening",
            "russia_cap=tightened_dynamic ($44.10 strict)",
            "shadow_fleet_premium=растёт (regulatory friction)",
        ]
    # brent-wti
    if scenario == "base":
        return ["mean-reverting arbitrage (model output)"]
    if scenario == "bear":
        return [
            "hormuz=partial_reopen → global supply нормализуется",
            "US_shale_premium=уходит",
        ]
    return [
        "hormuz=partial_closure → ME oil isolated",
        "US_shale_isolation_premium=растёт",
        "WTI lags Brent (relative supply advantage)",
    ]


# =============================================================================
# Interpretation
# =============================================================================


def _build_interpretation(
    pair: tuple[str, str],
    horizon: Horizon,
    current_spread: float,
    per_scenario: dict[str, SpreadScenarioEntry],
    method_tag: str,
) -> str:
    a, b = pair
    horizon_text = {
        Horizon.M1: "1 месяц",
        Horizon.M3: "3 месяца",
        Horizon.M6: "6 месяцев",
        Horizon.M12: "12 месяцев",
    }[horizon]

    method_human = {
        "schedule_anchored_ou": (
            "schedule-anchored OU (mean reverts к режимному discount из spread_schedule)"
        ),
        "series_diff_ou": (
            "OU на series_diff (mean-reverting natural через arbitrage)"
        ),
    }.get(method_tag, method_tag)

    parts = [
        f"**{a.title()} − {b.title()} spread, прогноз на {horizon_text}.** "
        f"Метод: {method_human}. Current spread: ${current_spread:.2f}/bbl. "
        f"См. ADR-0024 §«Spread per-scenario».",
    ]

    for scenario in ("base", "bear", "bull"):
        e = per_scenario[scenario]
        parts.append(
            f"**Сценарий {scenario}:** spread **${e.spread_value:.2f}** "
            f"(CI 80% [{e.ci_80.low:.2f}, {e.ci_80.high:.2f}]). {e.commentary}"
        )

    parts.append(
        "_Цитировать как:_ "
        f"`[Forecast: ou_regime_spread, scenario=base, CI 80%]` "
        "(или bear/bull для соответствующих сценариев)."
    )
    return "\n\n".join(parts)


__all__ = ["forecast_spread"]
