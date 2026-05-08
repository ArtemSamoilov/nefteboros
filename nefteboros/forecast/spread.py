"""Forecast spread tool — per-scenario для (Brent, Urals) и (Brent, WTI).

Реализация Track A2 из roadmap v2.1; обоснования — ADR-0023 §Q3.

Архитектура differentiated по парам:

  - **(brent, urals)** — schedule-based с per-scenario adjustment.
      Urals — DERIVED через spread_schedule.py (4 режима 2021-2026). Прямых
      Urals daily-цен после Feb 2025 в открытых источниках нет; модель на
      series_diff = brent − urals обучалась бы на синтетике (urals = brent − schedule).
      → используем schedule lookup напрямую с per-scenario logic:
          base: schedule mid (current cap_phase_2)
          bear: discount narrows (cap not binding в de-escalation)
          bull: discount widens (secondary sanctions risk)

  - **(brent, wti)** — модель на series_diff с per-scenario shift.
      Brent-WTI mean-reverting, обе серии реальные daily. Обучаем SARIMAX на
      series_diff, применяем per-scenario shift:
          base: model output напрямую
          bear: spread compresses (supply normalizes)
          bull: spread widens (US shale isolation premium когда ME blocked)

Closed list of pairs: `(brent, urals)`, `(brent, wti)`. Любая другая →
ForecastRefusal.

См. ADR-0023 §Q3 для полной мотивации.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import pandas as pd

from nefteboros.forecast.api import _fetch_history, _parse_horizon, _seed_for_reproducibility
from nefteboros.forecast.data.spread_schedule import (
    find_period_for_date,
    get_spread_for_date,
)
from nefteboros.forecast.models.sarimax import SARIMAXForecaster
from nefteboros.forecast.scenarios import (
    AS_OF_DATE,
    PRESET_SCENARIOS,
    parse_scenario,
)
from nefteboros.forecast.schema import (
    ConfidenceInterval,
    ForecastRefusal,
    Horizon,
    SpreadForecastResult,
    SpreadScenarioEntry,
)

logger = logging.getLogger(__name__)


# Closed list of supported pairs (asset_a, asset_b). Order matters: spread = a − b.
_VALID_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("brent", "urals"),
    ("brent", "wti"),
})


# =============================================================================
# Per-scenario adjustments
# =============================================================================
# Калибровка — ADR-0023 §Q3. Numbers — middle scenario expected discount/spread,
# (low, mid, high) для CI.

# (brent, urals): discount = brent − urals. Positive = urals ниже Brent.
# Calibration:
#   - base: cap_phase_2 schedule (current ~$17 mid с диапазоном $12-22)
#   - bear: cap not binding в de-escalation; discount sustracts к pre-cap norms ($5-12)
#     → подложить pre_war historical discount как proxy
#   - bull: secondary sanctions risk на shadow fleet растёт; discount expands
#     к war_shock 2022 levels ($20-30)
_BRENT_URALS_SCENARIO_DISCOUNT: dict[str, tuple[float, float, float]] = {
    # (low, mid, high) — discount в USD/bbl (positive = urals cheaper than brent)
    "base": (12.0, 17.0, 22.0),    # source: spread_schedule.py cap_phase_2
    "bear": (5.0, 8.5, 12.0),       # source: pre_war ~$1-2 + post-cap normalization buffer
    "bull": (20.0, 25.0, 30.0),     # source: war_shock 2022 quartile
}

# (brent, wti): spread = brent − wti. Positive = brent ahead of wti (typical).
# Mean-reverting around small premium. Per-scenario:
#   - base: model output (~$3-5 typical)
#   - bear: supply normalizes globally; spread compresses к pre-shock norm ($1-3)
#   - bull: Hormuz blocked → ME oil isolated; US shale relative supply advantage;
#     WTI lags behind Brent → spread widens ($5-8)
_BRENT_WTI_SCENARIO_SHIFT: dict[str, tuple[float, float, float]] = {
    # (low, mid, high) shift в USD/bbl относительно model output
    "base": (0.0, 0.0, 0.0),
    "bear": (-3.0, -2.0, -1.0),     # source: spread compression в спокойном режиме
    "bull": (2.0, 3.5, 5.0),        # source: US shale isolation premium при ME shock
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
    """Прогноз spread между двумя активами с per-scenario разбивкой.

    Возвращает три сценария (bear/base/bull) — creator: «один спред на все
    сценарии ни о чём; spread сам зависит от geopolitics, разный per scenario».

    Args:
        asset_a, asset_b: один из закрытого списка пар (brent, urals) или
                          (brent, wti).
        horizon: "1m" / "3m" / "6m" / "12m". Поддержка как у forecast().
        history_years: окно истории для модели (для wti pair).
        use_cache: использовать ли локальный кеш данных.

    Returns:
        SpreadForecastResult с per_scenario={"bear","base","bull"}, или
        ForecastRefusal если pair вне закрытого списка / horizon вне области.

    Raises:
        ValueError: если asset_a/asset_b неизвестны или другая невалидность.
        RuntimeError: если данные недоступны.
    """
    # 0. Reproducibility (см. ADR-0023 §A3).
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
                f"Поддерживаются: {valid_str}. См. ADR-0023 §Q3."
            ),
        )

    # 2. Validate horizon
    horizon_parsed = _parse_horizon(asset_a, horizon)
    if isinstance(horizon_parsed, ForecastRefusal):
        return horizon_parsed
    h: Horizon = horizon_parsed

    logger.info(
        "forecast_spread: pair=(%s, %s) horizon=%s",
        asset_a, asset_b, h.value,
    )

    # 3. Dispatch by pair
    if pair == ("brent", "urals"):
        return _forecast_brent_urals_spread(h)
    if pair == ("brent", "wti"):
        return _forecast_brent_wti_spread(h, history_years, use_cache)

    # Unreachable (закрыто проверкой выше), но чтобы Pylance не ругался
    raise RuntimeError(f"unhandled pair: {pair}")


# =============================================================================
# (brent, urals) — schedule-based с per-scenario adjustment
# =============================================================================


def _forecast_brent_urals_spread(horizon: Horizon) -> SpreadForecastResult:
    """Spread Brent − Urals из schedule с per-scenario логикой.

    Не модель — schedule lookup. Honest в commentary: «Urals — DERIVED, реальный
    daily обрезан Feb 2025; spread берётся из режимного расписания».
    """
    # Целевая дата — конец горизонта (горизонт_months × 30 дней approx)
    target_date = pd.Timestamp.now().normalize() + pd.DateOffset(
        months=horizon.months
    )

    per_scenario: dict[str, SpreadScenarioEntry] = {}

    for scenario_name in ("bear", "base", "bull"):
        low, mid, high = _BRENT_URALS_SCENARIO_DISCOUNT[scenario_name]

        # CI: 80% — uniform на [low, high] → ±0.8 quantile ≈ ±0.8×(high-low)/2
        # 95% — полный [low, high] (uniform extremes)
        half_range = (high - low) / 2
        ci_80 = ConfidenceInterval(
            level=0.80,
            low=mid - 0.8 * half_range,
            high=mid + 0.8 * half_range,
        )
        ci_95 = ConfidenceInterval(level=0.95, low=low, high=high)

        commentary = _commentary_for_brent_urals(scenario_name, low, mid, high)
        drivers = _drivers_for_brent_urals(scenario_name)

        per_scenario[scenario_name] = SpreadScenarioEntry(
            scenario=scenario_name,
            spread_value=mid,
            ci_80=ci_80,
            ci_95=ci_95,
            commentary=commentary,
            drivers=drivers,
        )

    interpretation = _build_brent_urals_interpretation(horizon, per_scenario)

    return SpreadForecastResult(
        asset_a="brent",
        asset_b="urals",
        horizon=horizon,
        target_date=target_date.to_pydatetime(),
        per_scenario=per_scenario,
        interpretation=interpretation,
        metadata={
            "method": "schedule_based",
            "schedule_period_at_target": find_period_for_date(target_date).name,
            "scenario_as_of": str(AS_OF_DATE),
            "rationale": (
                "Urals — DERIVED, реальный daily-spot обрезан Feb 2025. "
                "Spread берётся из режимного spread_schedule.py с per-scenario "
                "adjustment по логике cap binding/non-binding."
            ),
        },
    )


def _commentary_for_brent_urals(scenario: str, low: float, mid: float, high: float) -> str:
    if scenario == "base":
        return (
            f"Urals discount {mid:.0f} USD/bbl (диапазон {low:.0f}-{high:.0f}) — "
            f"текущий cap_phase_2 режим (G7 cap $47.60 / EU dynamic $44.10). "
            f"Shadow fleet absorbs логистический premium. Discount стабильный."
        )
    if scenario == "bear":
        return (
            f"При de-escalation Urals discount сужается до {mid:.0f} USD/bbl "
            f"(диапазон {low:.0f}-{high:.0f}). Если spot Brent падает к $75-90 "
            f"и cap $47.60 становится non-binding (spot < cap×ratio threshold), "
            f"shadow fleet premium снижается, российские sellers получают лучшие "
            f"цены. Возврат к pre-cap normality."
        )
    # bull
    return (
        f"При escalation Urals discount расширяется до {mid:.0f} USD/bbl "
        f"(диапазон {low:.0f}-{high:.0f}). Hormuz closure → secondary sanctions "
        f"risk на shadow fleet операторов растёт; китайские/индийские buyers "
        f"требуют дополнительный discount. Уровень близок к war_shock 2022."
    )


def _drivers_for_brent_urals(scenario: str) -> list[str]:
    if scenario == "base":
        return ["russia_cap=active ($47.60 G7 / $44.10 EU)", "shadow_fleet=stable"]
    if scenario == "bear":
        return [
            "hormuz=partial_reopen → spot Brent падает",
            "russia_cap=non-binding (spot < cap threshold)",
            "shadow_fleet_premium=снижается",
        ]
    return [
        "hormuz=full_closure → secondary sanctions tightening",
        "russia_cap=tightened_dynamic ($44.10 strict)",
        "shadow_fleet_premium=растёт (regulatory friction)",
    ]


def _build_brent_urals_interpretation(
    horizon: Horizon,
    per_scenario: dict[str, SpreadScenarioEntry],
) -> str:
    horizon_text = {
        Horizon.M1: "1 месяц",
        Horizon.M3: "3 месяца",
        Horizon.M6: "6 месяцев",
        Horizon.M12: "12 месяцев",
    }[horizon]

    parts = [
        f"**Brent − Urals spread, прогноз на {horizon_text}.** "
        "Метод: schedule-based с per-scenario adjustment. "
        "Urals — DERIVED актив (реальный daily-spot обрезан Feb 2025); "
        "discount берётся из режимного расписания (4 режима 2021-2026), "
        "per-scenario adjustment отражает cap binding logic.",
    ]
    for scenario in ("base", "bear", "bull"):
        e = per_scenario[scenario]
        parts.append(
            f"**Сценарий {scenario}:** discount **{e.spread_value:.1f}** USD/bbl "
            f"(CI 80% [{e.ci_80.low:.1f}, {e.ci_80.high:.1f}]). {e.commentary}"
        )
    parts.append(
        "⚠️ **Heavy political volatility.** Urals discount исторически "
        "скачкообразен (war shock 2022 расширил с $3 до $35 за 6 недель). "
        "При новых санкционных пакетах / отмене cap — выходит за рамки "
        "сценариев, требует пересмотра калибровки."
    )
    parts.append(
        "_Цитировать как:_ `[Forecast: spread_schedule, scenario=base, CI 80%]` "
        "(или bear/bull для соответствующих сценариев)."
    )
    return "\n\n".join(parts)


# =============================================================================
# (brent, wti) — модель на series разностей с per-scenario shift
# =============================================================================


def _forecast_brent_wti_spread(
    horizon: Horizon,
    history_years: float,
    use_cache: bool,
) -> SpreadForecastResult:
    """Spread Brent − WTI через модель на series_diff + per-scenario shift."""
    since = pd.Timestamp.now(tz="UTC").normalize() - pd.DateOffset(
        years=int(math.ceil(history_years))
    )
    brent_h = _fetch_history("brent", since=since, use_cache=use_cache)
    wti_h = _fetch_history("wti", since=since, use_cache=use_cache)

    # Align on common dates
    common_idx = brent_h.index.intersection(wti_h.index)
    if len(common_idx) < 60:
        raise RuntimeError(
            f"forecast_spread(brent, wti): мало общих точек "
            f"(got n={len(common_idx)}, need >=60)"
        )
    series_diff = (brent_h.loc[common_idx] - wti_h.loc[common_idx]).rename("brent_wti_spread")

    # Fit SARIMAX напрямую на spread (mean-reverting → быстро сходится)
    model = SARIMAXForecaster()
    model.fit(series_diff)
    raw_points = model.predict(horizon.months, levels=(0.80, 0.95))
    end = raw_points[-1]
    target_date = end.date

    base_spread = end.value
    base_ci_80 = end.ci_80
    base_ci_95 = end.ci_95

    per_scenario: dict[str, SpreadScenarioEntry] = {}
    for scenario_name in ("bear", "base", "bull"):
        s_low, s_mid, s_high = _BRENT_WTI_SCENARIO_SHIFT[scenario_name]
        spread_mid = base_spread + s_mid
        ci_80 = ConfidenceInterval(
            level=0.80,
            low=base_ci_80.low + s_low,
            high=base_ci_80.high + s_high,
        )
        ci_95 = ConfidenceInterval(
            level=0.95,
            low=base_ci_95.low + s_low,
            high=base_ci_95.high + s_high,
        )

        commentary = _commentary_for_brent_wti(scenario_name, spread_mid, s_low, s_high)
        drivers = _drivers_for_brent_wti(scenario_name)

        per_scenario[scenario_name] = SpreadScenarioEntry(
            scenario=scenario_name,
            spread_value=spread_mid,
            ci_80=ci_80,
            ci_95=ci_95,
            commentary=commentary,
            drivers=drivers,
        )

    interpretation = _build_brent_wti_interpretation(horizon, per_scenario, base_spread)

    return SpreadForecastResult(
        asset_a="brent",
        asset_b="wti",
        horizon=horizon,
        target_date=target_date,
        per_scenario=per_scenario,
        interpretation=interpretation,
        metadata={
            "method": "sarimax_on_series_diff",
            "raw_model_spread": base_spread,
            "data_n_common_points": len(common_idx),
            "data_first_observation": str(common_idx.min().date()),
            "data_last_observation": str(common_idx.max().date()),
            "scenario_as_of": str(AS_OF_DATE),
            "rationale": (
                "SARIMAX на series_diff = brent − wti. Spread mean-reverting; "
                "обе серии реальные daily. Per-scenario shift калиброван по "
                "US shale isolation logic в shock-режиме."
            ),
        },
    )


def _commentary_for_brent_wti(scenario: str, spread_mid: float, s_low: float, s_high: float) -> str:
    if scenario == "base":
        return (
            f"Brent-WTI spread {spread_mid:.2f} USD/bbl — model output напрямую "
            f"(SARIMAX на series_diff). Текущий shock-режим уже учтён в "
            f"наблюдаемом spread'е."
        )
    if scenario == "bear":
        return (
            f"При de-escalation spread compresses к pre-shock norm "
            f"{spread_mid:.2f} USD/bbl (shift {s_low:+.1f}..{s_high:+.1f}). "
            f"Глобальная supply нормализуется, US shale perturbation premium "
            f"уходит."
        )
    return (
        f"При escalation Hormuz blocked → ME oil isolated. US shale получает "
        f"relative supply advantage, WTI lags за Brent. Spread расширяется "
        f"до {spread_mid:.2f} USD/bbl (shift {s_low:+.1f}..{s_high:+.1f})."
    )


def _drivers_for_brent_wti(scenario: str) -> list[str]:
    if scenario == "base":
        return ["model output (mean-reverting SARIMAX)"]
    if scenario == "bear":
        return [
            "hormuz=partial_reopen → global supply нормализуется",
            "US_shale_premium=уходит",
        ]
    return [
        "hormuz=full_closure → ME oil isolated",
        "US_shale_isolation_premium=растёт",
        "WTI lags Brent (relative supply advantage)",
    ]


def _build_brent_wti_interpretation(
    horizon: Horizon,
    per_scenario: dict[str, SpreadScenarioEntry],
    raw_model_spread: float,
) -> str:
    horizon_text = {
        Horizon.M1: "1 месяц",
        Horizon.M3: "3 месяца",
        Horizon.M6: "6 месяцев",
        Horizon.M12: "12 месяцев",
    }[horizon]

    parts = [
        f"**Brent − WTI spread, прогноз на {horizon_text}.** "
        f"Метод: SARIMAX на series_diff (mean-reverting; обе серии реальные daily). "
        f"Model output: {raw_model_spread:.2f} USD/bbl.",
    ]
    for scenario in ("base", "bear", "bull"):
        e = per_scenario[scenario]
        parts.append(
            f"**Сценарий {scenario}:** spread **{e.spread_value:.2f}** USD/bbl "
            f"(CI 80% [{e.ci_80.low:.2f}, {e.ci_80.high:.2f}]). {e.commentary}"
        )
    parts.append(
        "ℹ️ Brent-WTI исторически mean-reverting around small premium "
        "($2-5 typical). Большие отклонения возникают при региональных shocks "
        "(US shale boom, Cushing inventory pile-ups, ME disruptions)."
    )
    parts.append(
        "_Цитировать как:_ `[Forecast: sarimax, scenario=base, CI 80%]` "
        "(или bear/bull)."
    )
    return "\n\n".join(parts)


__all__ = ["forecast_spread"]
