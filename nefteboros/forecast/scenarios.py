"""Сценарный режим forecast tool — драйверы, presets, shift-логика.

Реализация Track A1 из roadmap v2.1; обоснования и калибровка — ADR-0023.

Архитектура (вариант A из обсуждения 2026-05-08):
  - base = current shock state (Hormuz blocked, Iran 0.4 mbpd, MOU pending),
    anchored to spot price через transparent observation-shift к ensemble output.
  - bear = de-escalation (MOU signed, Hormuz reopens, Iran partial lift) → ~$75-90.
  - bull = escalation (Hormuz fully closed, regional war expands) → ~$145-175.

Драйверы (5 шт.) хранят `mbpd_shift` и `usd_per_bbl_shift` per state. Конвертация
mbpd → $/bbl откалибрована на ~$10-15/bbl per 1 mbpd (Kilian classic + Goldman
severe scenario implicit).

Применимость:
  - Brent — все драйверы apply (full sensitivity).
  - WTI — apply, но Hormuz эффект ослаблен (US shale partial isolation premium).
  - Urals / ESPO / urals_minfin_blend — derived: scenario applies к base Brent,
    derived layer наследует через `derive_*_forecast()`.
  - Газ (TTF, henry_hub), russian energy proxy (moexog/gazp/nvtk) — сценарии
    **не применяются** в v2.1; runtime warning в interpretation.

Snapshot 2026-05-08 — заморожен в CURRENT_STATE_2026_05; обновляется при крупных
событиях (см. ADR-0023 §«когда обновлять snapshot»).

См. ADR-0023 — полная карта решений и research-калибровки.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Snapshot 2026-05-08 — заморожен (см. ADR-0023)
# =============================================================================

AS_OF_DATE: date = date(2026, 5, 8)
"""Дата фиксации snapshot. При расхождении с runtime > 14 дней — warning."""

REVIEW_AFTER_DAYS: int = 14
"""Через сколько дней после AS_OF_DATE считать snapshot потенциально устаревшим."""

FORECAST_RANDOM_STATE: int = 42
"""Глобальный random_state для всего forecast pipeline (A3 reproducibility).

Используется в: GBR.random_state (xgboost_m.py), seed в forecast() и
forecast_spread() через _seed_for_reproducibility() в api.py."""


CURRENT_STATE_2026_05: dict[str, str] = {
    # research-verified, 2026-05-08; источники в ADR-0023
    "hormuz": "blocked",
    "iran_sanctions": "maximum_pressure_active",
    "opec_plus": "gradual_unwinding",  # +206k bpd/мес начиная с May 2026
    "russia_cap": "active",  # $47.60 G7 / $44.10 EU dynamic — оба активны
    "china_demand": "base",  # +0.198 mbpd y/y per IEA
    "brent_spot_observed": "100.06",  # CNBC 2026-05-07
}


# =============================================================================
# Типы драйверов и сценариев
# =============================================================================

DriverHormuz = Literal["blocked", "partial_reopen", "full_reopen", "full_closure"]
DriverIran = Literal[
    "maximum_pressure_active", "partial_lift", "full_lift", "further_tightening"
]
DriverOpecPlus = Literal["gradual_unwinding", "accelerated_unwinding", "extended_cuts"]
DriverRussiaCap = Literal["active", "tightened_dynamic", "removed"]
DriverChina = Literal["weak", "base", "strong"]

ScenarioName = Literal["base", "bear", "bull"]


# =============================================================================
# Driver lookup — (state) → ($/bbl shift low, mid, high)
# =============================================================================
# Источники калибровки — в ADR-0023 §Q2. Каждое число с # source: <ref>.
# Знак: положительный = поддержка цены, отрицательный = давление вниз.
# (low, mid, high) трактуется как uniform на [low, high] для CI-convolution.

# Hormuz — strait через который идёт ~25% world seaborne oil
# Калибровка: full closure — Goldman severe Q4 implies +$25 на 2 mbpd,
# но full closure это -3..-5 mbpd persistent → +$50..+$75
_HORMUZ_SHIFT: dict[DriverHormuz, tuple[float, float, float]] = {
    "blocked": (0.0, 0.0, 0.0),               # current state, anchor reference
    "partial_reopen": (-22.0, -18.0, -15.0),  # source: Goldman post-ceasefire $90 vs $99 = -$9..-$15 partial
    "full_reopen": (-45.0, -37.0, -30.0),     # source: full normalization, отыгрывает весь shock-premium
    "full_closure": (50.0, 62.0, 75.0),       # source: Bloomberg Hormuz closure model; Goldman severe extrapolated
}

# Iran экспорт: current = 0.4 mbpd vs pre-war 1.6 mbpd = -1.2 mbpd off market
_IRAN_SHIFT: dict[DriverIran, tuple[float, float, float]] = {
    "maximum_pressure_active": (0.0, 0.0, 0.0),  # current
    "partial_lift": (-10.0, -8.0, -6.0),          # +0.6 mbpd (return к ~1.0); $10/bbl per 1 mbpd
    "full_lift": (-18.0, -15.0, -12.0),           # +1.2 mbpd (return к 1.6); Kilian classic
    "further_tightening": (2.0, 2.5, 3.0),        # -0.2 mbpd (already collapsed, marginal effect)
}

# OPEC+ — 1.65 mbpd voluntary cuts; в апреле 2026 начали +206k bpd/мес unwinding
_OPEC_PLUS_SHIFT: dict[DriverOpecPlus, tuple[float, float, float]] = {
    "gradual_unwinding": (0.0, 0.0, 0.0),       # current
    "accelerated_unwinding": (-15.0, -12.5, -10.0),  # +1.0..+1.5 mbpd ahead of schedule
    "extended_cuts": (5.0, 6.5, 8.0),            # source: re-tighten +0.5 mbpd cuts; price defense
}

# Russia cap: $47.60 (G7) / $44.10 (EU dynamic) — оба активны
# Эффект через price (не volume) — повышает logistics/shadow fleet premium
_RUSSIA_CAP_SHIFT: dict[DriverRussiaCap, tuple[float, float, float]] = {
    "active": (0.0, 0.0, 0.0),                  # current
    "tightened_dynamic": (3.0, 4.0, 5.0),        # source: Bruegel — strict $44.10 enforcement → +supply friction
    "removed": (-8.0, -6.5, -5.0),               # +0.5 mbpd Russian export normalizes
}

# China — IEA OMR Feb 2026: +0.198 mbpd 2026 (slow recovery)
_CHINA_SHIFT: dict[DriverChina, tuple[float, float, float]] = {
    "weak": (-5.0, -4.0, -3.0),                  # source: -0.4 mbpd from base
    "base": (0.0, 0.0, 0.0),                      # current trajectory
    "strong": (3.0, 4.0, 5.0),                    # +0.4 mbpd above base
}


# =============================================================================
# ScenarioParams — комбинация driver states
# =============================================================================


class ScenarioParams(BaseModel):
    """Комбинация driver states для одного сценария.

    Используется как:
    - один из PRESET_SCENARIOS ("base", "bear", "bull")
    - custom-комбинация от пользователя

    Линейная суммация driver shifts. Preset калиброван как целое; custom помечается
    `interpretation`-ом «оценочный, не cross-validated».

    См. ADR-0023 §Q2.
    """

    model_config = ConfigDict(frozen=True)

    hormuz: DriverHormuz = "blocked"
    iran: DriverIran = "maximum_pressure_active"
    opec_plus: DriverOpecPlus = "gradual_unwinding"
    russia_cap: DriverRussiaCap = "active"
    china: DriverChina = "base"

    # Источник: preset / custom — для honest reporting
    is_preset: bool = False


PRESET_SCENARIOS: dict[ScenarioName, ScenarioParams] = {
    "base": ScenarioParams(
        # Текущий shock-режим, anchored to spot ($100)
        hormuz="blocked",
        iran="maximum_pressure_active",
        opec_plus="gradual_unwinding",
        russia_cap="active",
        china="base",
        is_preset=True,
    ),
    "bear": ScenarioParams(
        # De-escalation: MOU подписан, Hormuz reopens частично, Iran частичный lift
        hormuz="partial_reopen",        # -$15..-$22
        iran="partial_lift",            # -$6..-$10
        opec_plus="extended_cuts",      # +$5..+$8 (защита от падения)
        russia_cap="active",
        china="base",
        is_preset=True,
        # Net delta: -$15..-$25 → если base ~$100 → bear ~$75-90
        # Соответствует: Goldman post-ceasefire $90, JPM $60 (нижняя граница)
    ),
    "bull": ScenarioParams(
        # Escalation: MOU breaks, Hormuz fully closed, regional war expands
        hormuz="full_closure",          # +$50..+$75
        iran="further_tightening",      # +$2..+$3
        opec_plus="gradual_unwinding",
        russia_cap="tightened_dynamic", # +$3..+$5
        china="weak",                   # -$3..-$5 (recession risk)
        is_preset=True,
        # Net delta: +$45..+$75 → если base ~$100 → bull ~$145-175
        # Bull агрессивнее Goldman severe ($115 при 2 mbpd loss) — отражает
        # full closure (>2 mbpd persistent), не просто 2 mbpd loss
    ),
}


# =============================================================================
# Asset applicability — какие assets поддерживают сценарии в v2.1
# =============================================================================
# Калибровка _*_SHIFT — для глобальной нефти (Brent reference). Для других:
#   - WTI: scenarios apply, но Hormuz эффект ослаблен (US shale partial isolation
#     premium); умножаем Hormuz shift на 0.6 для WTI.
#   - Urals/ESPO/blend: DERIVED через Brent forecast; scenario applies к Brent,
#     derived layer наследует автоматически (forecast.api._forecast_derived).
#   - Газ (TTF, henry_hub): cross-effects от нефтяных шоков нелинейны; v2.1
#     scenario не применяется, runtime warning.
#   - Russian energy proxy (moexog, gazp, nvtk): акции — отдельная динамика,
#     scenario не применяется в v2.1.

_SCENARIO_APPLICABLE_FULL: frozenset[str] = frozenset({
    "brent",
    "urals",
    "espo",
    "urals_minfin_blend",
    # urals/espo/blend — DERIVED, scenario propagates через base Brent forecast
})

_SCENARIO_APPLICABLE_REDUCED: frozenset[str] = frozenset({
    "wti",
    # WTI получает ослабленный Hormuz эффект (US shale partial isolation)
})

_HORMUZ_REDUCED_FACTOR_WTI: float = 0.6
"""Множитель Hormuz shift для WTI — отражает US shale partial isolation."""


def is_scenario_applicable(asset_id: str) -> bool:
    """True — сценарии можно применять; False — runtime warning + scenario ignored."""
    return (
        asset_id in _SCENARIO_APPLICABLE_FULL
        or asset_id in _SCENARIO_APPLICABLE_REDUCED
    )


# =============================================================================
# Public API — compute_scenario_delta
# =============================================================================


@dataclass(frozen=True)
class ScenarioDelta:
    """Итоговый shift сценария от base ensemble output для одного актива.

    `low`, `mid`, `high` — диапазон shift'а в $/bbl (uniform на [low, high]).
    `driver_breakdown` — per-driver вклад для interpretation/диагностики.
    """

    low: float
    mid: float
    high: float
    driver_breakdown: dict[str, tuple[float, float, float]]


def compute_scenario_delta(
    params: ScenarioParams,
    asset_id: str,
) -> ScenarioDelta:
    """Посчитать итоговый shift сценария относительно base scenario.

    Логика:
    1. Для base scenario — все driver shifts = 0 (по построению PRESET_SCENARIOS["base"]).
    2. Для bear/bull/custom — суммируем shifts от каждого driver per state.
    3. Для WTI — Hormuz shift умножен на _HORMUZ_REDUCED_FACTOR_WTI.

    Args:
        params: ScenarioParams с driver states.
        asset_id: для применения asset-specific корректировок (WTI Hormuz).

    Returns:
        ScenarioDelta с (low, mid, high) и breakdown.

    Raises:
        ValueError: если asset_id не applicable (используй is_scenario_applicable).
    """
    if not is_scenario_applicable(asset_id):
        raise ValueError(
            f"scenario не применяется к asset_id={asset_id!r} в v2.1 "
            f"(см. ADR-0023). Используй is_scenario_applicable() для проверки."
        )

    breakdown: dict[str, tuple[float, float, float]] = {}

    # Hormuz — asset-aware
    h_low, h_mid, h_high = _HORMUZ_SHIFT[params.hormuz]
    if asset_id in _SCENARIO_APPLICABLE_REDUCED:
        f = _HORMUZ_REDUCED_FACTOR_WTI
        h_low, h_mid, h_high = h_low * f, h_mid * f, h_high * f
    breakdown["hormuz"] = (h_low, h_mid, h_high)

    breakdown["iran"] = _IRAN_SHIFT[params.iran]
    breakdown["opec_plus"] = _OPEC_PLUS_SHIFT[params.opec_plus]
    breakdown["russia_cap"] = _RUSSIA_CAP_SHIFT[params.russia_cap]
    breakdown["china"] = _CHINA_SHIFT[params.china]

    # Линейная суммация
    total_low = sum(t[0] for t in breakdown.values())
    total_mid = sum(t[1] for t in breakdown.values())
    total_high = sum(t[2] for t in breakdown.values())

    return ScenarioDelta(
        low=total_low,
        mid=total_mid,
        high=total_high,
        driver_breakdown=breakdown,
    )


# =============================================================================
# Base anchor shift — observation-anchored correction для base scenario
# =============================================================================


@dataclass(frozen=True)
class BaseAnchor:
    """Observation-anchored shift для base scenario.

    Раскрывает gap между model output и spot. Не из модели — из observation.
    Прозрачен в metadata + interpretation.

    Используется только для scenario="base" — bear/bull уже от base через delta.
    """

    raw_model_value: float       # ensemble mean без shift (model belief)
    observed_spot: float          # last spot из history (proxy для current)
    anchor_shift: float           # = observed_spot - raw_model_value


def compute_base_anchor(
    raw_model_value: float,
    observed_spot: float,
) -> BaseAnchor:
    """Посчитать anchor shift для base scenario.

    На горизонтах >1m модель уходит от spot из-за mean-reversion / drift; для
    base scenario мы хотим, чтобы значение **на target_date** отражало текущий
    shock-режим (если он сохранится). Anchor — это (observed_spot - raw_model)
    добавленный к raw_model_value, чтобы start был = observed_spot.

    NB: shift применяется только к value/CI на target_date, не к траектории.
    История модели не модифицируется.

    Args:
        raw_model_value: model mean output без shift.
        observed_spot: последняя наблюдаемая цена (history.iloc[-1]).

    Returns:
        BaseAnchor с прозрачным shift'ом.
    """
    return BaseAnchor(
        raw_model_value=raw_model_value,
        observed_spot=observed_spot,
        anchor_shift=observed_spot - raw_model_value,
    )


# =============================================================================
# Helpers для парсинга и интерпретации
# =============================================================================


def parse_scenario(
    raw: Optional[str | ScenarioParams],
) -> ScenarioParams:
    """Парсинг scenario аргумента forecast() в ScenarioParams.

    None | "base" → PRESET_SCENARIOS["base"]
    "bear" / "bull" → PRESET_SCENARIOS[name]
    ScenarioParams → as-is
    """
    if raw is None:
        return PRESET_SCENARIOS["base"]
    if isinstance(raw, ScenarioParams):
        return raw
    if isinstance(raw, str):
        if raw not in PRESET_SCENARIOS:
            valid = ", ".join(sorted(PRESET_SCENARIOS.keys()))
            raise ValueError(
                f"Unknown scenario name {raw!r}. Valid presets: {valid}. "
                f"Custom — передай ScenarioParams напрямую."
            )
        return PRESET_SCENARIOS[raw]
    raise TypeError(
        f"scenario must be None, str, or ScenarioParams; got {type(raw).__name__}"
    )


def get_preset_scenario_name(params: ScenarioParams) -> Optional[ScenarioName]:
    """Если params совпадает с одним из PRESET_SCENARIOS — вернуть имя; иначе None.

    Используется для citation format: `scenario=base|bear|bull` для preset,
    `scenario=custom` для custom-комбинации.
    """
    for name, preset in PRESET_SCENARIOS.items():
        # Сравниваем по driver полям, не по is_preset (custom может случайно
        # совпасть с preset по полям — это OK, считаем preset)
        if (
            params.hormuz == preset.hormuz
            and params.iran == preset.iran
            and params.opec_plus == preset.opec_plus
            and params.russia_cap == preset.russia_cap
            and params.china == preset.china
        ):
            return name
    return None


def scenario_label(params: ScenarioParams) -> str:
    """Метка сценария для citation: 'base'/'bear'/'bull' или 'custom'."""
    name = get_preset_scenario_name(params)
    return name if name is not None else "custom"


__all__ = [
    "AS_OF_DATE",
    "REVIEW_AFTER_DAYS",
    "FORECAST_RANDOM_STATE",
    "CURRENT_STATE_2026_05",
    "DriverHormuz",
    "DriverIran",
    "DriverOpecPlus",
    "DriverRussiaCap",
    "DriverChina",
    "ScenarioName",
    "ScenarioParams",
    "PRESET_SCENARIOS",
    "ScenarioDelta",
    "BaseAnchor",
    "compute_scenario_delta",
    "compute_base_anchor",
    "is_scenario_applicable",
    "parse_scenario",
    "get_preset_scenario_name",
    "scenario_label",
]
