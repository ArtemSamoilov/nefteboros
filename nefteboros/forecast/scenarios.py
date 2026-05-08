"""Сценарный режим forecast tool — Ornstein-Uhlenbeck per scenario.

Реализация Track A1 из roadmap v2.1; обоснования и калибровка — ADR-0024.

Концептуально (термостат-аналогия):
  - μ — long-run target per scenario (что 22°C для термостата)
  - θ — speed of reversion (мощность батареи)
  - σ — volatility (сквозняки)
  - μ(t) = μ₀ × (1 + i·t) — target дрейфует с инфляцией

OU process:
  dS = θ(μ(t) - S) dt + σ dW
  E[S_t]   = μ(t) + (S_0 - μ_0) × exp(-θt)
  Var[S_t] = σ²/(2θ) × (1 - exp(-2θt))     ← bounded при t→∞

Это даёт actionable CI на длинных horizons (структурное свойство commodity:
floor cost-of-production, ceiling demand-destruction → mean reversion).

Применимость:
  - Нефть (brent, wti, urals, espo, urals_minfin_blend)
  - Газ (henry_hub, ttf)
  - MOEX nefтегаз proxy (moexog, gazp, nvtk) — INVERTED bull
  - opec_basket — fetcher не реализован (P1 backlog)

Snapshot 2026-05-08 — заморожен в CURRENT_STATE_2026_05; обновляется при
крупных событиях (см. ADR-0024 §«когда обновлять snapshot»).

См. ADR-0024 — полная карта решений и research-калибровки.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


# =============================================================================
# Snapshot 2026-05-08 — заморожен (см. ADR-0024)
# =============================================================================

AS_OF_DATE: date = date(2026, 5, 8)
"""Дата фиксации snapshot. При расхождении с runtime > 14 дней — warning."""

REVIEW_AFTER_DAYS: int = 14
"""Через сколько дней после AS_OF_DATE считать snapshot потенциально устаревшим."""

FORECAST_RANDOM_STATE: int = 42
"""Глобальный random_state для всего forecast pipeline (A3 reproducibility).

OU detrministic by construction; random_state нужен для backtest infrastructure."""


CURRENT_STATE_2026_05: dict[str, str] = {
    # Snapshot текущего состояния рынка (2026-05-08, research-verified).
    # Используется для interpretation context, не для расчётов (расчёты — через
    # ASSET_PARAMS calibration).
    "hormuz": "blocked",
    "iran_sanctions": "maximum_pressure_active",
    "opec_plus": "gradual_unwinding",       # +206k bpd/мес начиная с May 2026
    "russia_cap": "active",                  # $47.60 G7 / $44.10 EU dynamic
    "china_demand": "base",                  # +0.198 mbpd y/y per IEA
    "brent_spot_observed": "100.06",         # CNBC 2026-05-07
}


# =============================================================================
# Scenario types
# =============================================================================

ScenarioName = Literal["base", "bear", "bull"]
SCENARIO_NAMES: tuple[ScenarioName, ...] = ("base", "bear", "bull")


class ScenarioParams(BaseModel):
    """Идентификатор сценария.

    В v2.1 (ADR-0024) сценарий задаётся именем (base/bear/bull); per-driver
    custom комбинации в backlog v2.2. Driver semantics описаны в interpretation.
    """

    model_config = ConfigDict(frozen=True)

    name: ScenarioName = "base"


PRESET_SCENARIOS: dict[ScenarioName, ScenarioParams] = {
    "base": ScenarioParams(name="base"),
    "bear": ScenarioParams(name="bear"),
    "bull": ScenarioParams(name="bull"),
}


# =============================================================================
# OU calibration parameters per asset per scenario (см. ADR-0024)
# =============================================================================


@dataclass(frozen=True)
class OUParams:
    """Параметры OU процесса для одного (asset, scenario) пары.

    Attributes:
        mu_0: long-run target в snapshot date (USD/bbl или unit актива).
        theta: speed of reversion (1/year). Half-life = ln(2)/theta years.
        sigma: annualized volatility as fraction of spot (e.g. 0.20 для 20%).
        inflation: nominal inflation rate per year (e.g. 0.05 для 5%).
        Mu drifts as μ(t) = μ_0 × (1 + inflation × t).
    """

    mu_0: float
    theta: float
    sigma: float
    inflation: float


# Asset → (bear params, base params, bull params)
# Калибровка research-verified, см. ADR-0024 §«Calibration tables».
# При обновлении — единое место правки (по convention каждое значение с # source).

# Нефть
_OIL_INFLATION = 0.05  # nominal: long-run real growth ~2% + CPI ~3% (research)
ASSET_PARAMS: dict[str, dict[ScenarioName, OUParams]] = {
    # ----- Нефть -----
    "brent": {
        # source bear: Reuters Feb 2026 consensus $63.85 + Goldman post-cease $90 + long-run real $58 → $70
        # source base: spot $100, Goldman pre-cease $99 → $98 как «текущее shock equilibrium»
        # source bull: Goldman severe Q4 $115 при 2 mbpd loss + extension → $120
        # source theta: bear half-life 2.8mo (calm regime fast); base 4.2mo; bull 5.5mo (turbulent slow)
        # source sigma: pre_war 2021 ~22%, war_shock ~55%, cap_phase ~28%, OVX current ~70% — regime mid
        "bear": OUParams(mu_0=70.0,  theta=3.0, sigma=0.20, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=98.0,  theta=2.0, sigma=0.25, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=120.0, theta=1.5, sigma=0.30, inflation=_OIL_INFLATION),
    },
    "wti": {
        # WTI ~ Brent − $5 typical premium; same volatility regime
        "bear": OUParams(mu_0=66.0,  theta=3.0, sigma=0.20, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=94.0,  theta=2.0, sigma=0.25, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=115.0, theta=1.5, sigma=0.30, inflation=_OIL_INFLATION),
    },
    "urals": {
        # Urals = Brent − sanction-discount per scenario:
        # bear: Brent$70 − bear discount $8 = $62
        # base: Brent$98 − base discount $17 (cap_phase_2) = $81
        # bull: Brent$120 − bull discount $25 = $95
        # +sigma adjustment +2pp для spread variability
        "bear": OUParams(mu_0=62.0, theta=3.0, sigma=0.22, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=81.0, theta=2.0, sigma=0.27, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=95.0, theta=1.5, sigma=0.32, inflation=_OIL_INFLATION),
    },
    "espo": {
        # ESPO ~ Brent − $5 typical (Asian premium pre-war, normalize sanctions)
        "bear": OUParams(mu_0=65.0,  theta=3.0, sigma=0.21, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=92.0,  theta=2.0, sigma=0.26, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=113.0, theta=1.5, sigma=0.31, inflation=_OIL_INFLATION),
    },
    "urals_minfin_blend": {
        # 0.78 × urals + 0.22 × espo (Минфин НДПИ-формула с 2025-01)
        "bear": OUParams(mu_0=63.0, theta=3.0, sigma=0.22, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=83.0, theta=2.0, sigma=0.27, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=99.0, theta=1.5, sigma=0.32, inflation=_OIL_INFLATION),
    },

    # ----- Газ -----
    # source: HH 2022 = 91% real vol, 2023 = 69%; TTF 2022 extreme. Газ inherently
    # more volatile, slower mean reversion (less liquid markets, regime persists).
    # Inflation 4%/y — gas substitutable (electric heating, renewables) → lower passthrough
    "henry_hub": {
        "bear": OUParams(mu_0=2.30, theta=2.0, sigma=0.35, inflation=0.04),
        "base": OUParams(mu_0=2.77, theta=1.5, sigma=0.45, inflation=0.04),
        "bull": OUParams(mu_0=3.50, theta=1.0, sigma=0.55, inflation=0.04),
    },
    "ttf": {
        "bear": OUParams(mu_0=35.0, theta=2.0, sigma=0.35, inflation=0.04),
        "base": OUParams(mu_0=43.0, theta=1.5, sigma=0.45, inflation=0.04),
        "bull": OUParams(mu_0=55.0, theta=1.0, sigma=0.50, inflation=0.04),
    },

    # ----- Российский нефтегаз proxy (INVERTED bull — escalation hurts equity) -----
    # source: Q1 2022 GAZP −50% YTD при Brent +50% — Russia-specific factors
    # (sanctions, RUB outflow) доминируют над commodity tailwind.
    # Inflation 10%/y — RUB equity nominal: CBR rate 16-18% + страновая премия;
    # частично включает рост цен фон.
    "moexog": {
        "bear": OUParams(mu_0=7200.0, theta=2.0, sigma=0.18, inflation=0.10),
        "base": OUParams(mu_0=6700.0, theta=1.5, sigma=0.22, inflation=0.10),
        "bull": OUParams(mu_0=5500.0, theta=1.0, sigma=0.30, inflation=0.10),
    },
    "gazp": {
        "bear": OUParams(mu_0=130.0, theta=2.0, sigma=0.20, inflation=0.10),
        "base": OUParams(mu_0=117.0, theta=1.5, sigma=0.25, inflation=0.10),
        "bull": OUParams(mu_0=85.0,  theta=1.0, sigma=0.35, inflation=0.10),
    },
    "nvtk": {
        "bear": OUParams(mu_0=1280.0, theta=2.0, sigma=0.22, inflation=0.10),
        "base": OUParams(mu_0=1124.0, theta=1.5, sigma=0.27, inflation=0.10),
        "bull": OUParams(mu_0=820.0,  theta=1.0, sigma=0.40, inflation=0.10),
    },
}


# =============================================================================
# OU forecast computation
# =============================================================================


@dataclass(frozen=True)
class OUForecast:
    """Результат OU forecast для одной точки (target_date)."""

    mid: float
    ci_80_low: float
    ci_80_high: float
    ci_95_low: float
    ci_95_high: float
    # Diagnostic
    mu_t: float          # μ(t) = target с учётом inflation drift
    raw_anchor: float    # spot − μ_0 (отклонение от long-run target в snapshot)


_Z80 = 1.282
_Z95 = 1.960


def compute_ou_forecast(
    spot: float,
    params: OUParams,
    horizon_months: int,
    *,
    clip_negative: bool = False,
) -> OUForecast:
    """Posчитать OU forecast для одного scenario × horizon.

    Args:
        spot: текущая spot цена.
        params: OUParams для scenario × asset.
        horizon_months: 1/3/6/12.
        clip_negative: если True — clip ci_low к 0 для price-positive активов.

    Returns:
        OUForecast с mid + CI 80/95 + диагностикой.
    """
    t = horizon_months / 12.0  # convert to years
    mu_t = params.mu_0 * (1 + params.inflation * t)
    mid = mu_t + (spot - params.mu_0) * math.exp(-params.theta * t)

    # Variance bounded: σ²/(2θ) × (1 - exp(-2θt))
    sigma_dollar = params.sigma * spot
    var = (sigma_dollar ** 2 / (2 * params.theta)) * (1 - math.exp(-2 * params.theta * t))
    sd = math.sqrt(var)

    ci_80_low = mid - _Z80 * sd
    ci_80_high = mid + _Z80 * sd
    ci_95_low = mid - _Z95 * sd
    ci_95_high = mid + _Z95 * sd

    if clip_negative:
        ci_80_low = max(0.0, ci_80_low)
        ci_95_low = max(0.0, ci_95_low)
        mid = max(0.0, mid)

    return OUForecast(
        mid=mid,
        ci_80_low=ci_80_low,
        ci_80_high=ci_80_high,
        ci_95_low=ci_95_low,
        ci_95_high=ci_95_high,
        mu_t=mu_t,
        raw_anchor=spot - params.mu_0,
    )


# =============================================================================
# Asset applicability + helpers
# =============================================================================


def is_scenario_applicable(asset_id: str) -> bool:
    """True если asset имеет OU calibration в ASSET_PARAMS."""
    return asset_id in ASSET_PARAMS


def get_ou_params(asset_id: str, scenario: ScenarioName) -> OUParams:
    """Lookup OU params для (asset, scenario). Raises KeyError если не найдено."""
    if asset_id not in ASSET_PARAMS:
        raise KeyError(
            f"OU calibration отсутствует для asset_id={asset_id!r}. "
            f"Доступны: {sorted(ASSET_PARAMS.keys())}"
        )
    return ASSET_PARAMS[asset_id][scenario]


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
                f"Unknown scenario name {raw!r}. Valid: {valid}."
            )
        return PRESET_SCENARIOS[raw]
    raise TypeError(
        f"scenario must be None, str, or ScenarioParams; got {type(raw).__name__}"
    )


def scenario_label(params: ScenarioParams) -> str:
    """Метка сценария для citation: 'base'/'bear'/'bull'."""
    return params.name


# =============================================================================
# Driver flags decomposition (для interpretation, не для расчётов)
# =============================================================================
# Расчёт идёт через ASSET_PARAMS (μ, θ, σ, infl). Но agent/user может хотеть
# понять «почему μ_bear именно $70?» — для этого FLAGS_DECOMPOSITION даёт
# explicit attribution для каждого scenario.

FLAGS_DECOMPOSITION: dict[ScenarioName, dict[str, str]] = {
    "base": {
        "hormuz": "blocked (-3 mbpd off market, current state)",
        "iran": "maximum_pressure_active (Iran exports 0.4 mbpd vs pre-shock 1.6)",
        "opec_plus": "gradual_unwinding (1.65 mbpd cuts, +206k bpd/мес unwind)",
        "russia_cap": "active ($47.60 G7 / $44.10 EU dynamic, current)",
        "china_demand": "base (+0.198 mbpd y/y per IEA)",
        "summary": "Текущее shock equilibrium. Hormuz crisis сохраняется, no resolution. "
                   "Brent ~$100, Goldman pre-ceasefire view.",
    },
    "bear": {
        "hormuz": "partial_reopen (+1.5 mbpd back online, MOU signed)",
        "iran": "partial_lift (Iran exports +0.6 mbpd, sanctions partial)",
        "opec_plus": "extended_cuts (-0.5 mbpd, defend prices)",
        "russia_cap": "active (cap binding decreases as spot falls)",
        "china_demand": "base (+0.2 mbpd, no demand shock)",
        "summary": "De-escalation: MOU подписан, Hormuz reopens, Iran частично возвращается. "
                   "Net supply +1.6 mbpd → Brent движется к pre-shock norm $60-70. "
                   "Match: Goldman post-ceasefire $90, JPM $60 floor.",
    },
    "bull": {
        "hormuz": "partial_closure (-2 mbpd more off market, secondary sanctions tighten)",
        "iran": "further_tightening (-0.2 mbpd, additional pressure)",
        "opec_plus": "accelerated_unwinding (+0.5 mbpd faster)",
        "russia_cap": "tightened_dynamic ($44.10 strict enforcement)",
        "china_demand": "weak (-0.4 mbpd, price-induced demand softening)",
        "summary": "Escalation: shock усиливается. Net supply -1.7 mbpd, China -0.4 mbpd → "
                   "deficit -1.3 mbpd × Kilian elasticity ($12/mbpd) = +$16. "
                   "Match: Goldman severe Q4 $115 при 2 mbpd loss, наш bull ~$120.",
    },
}


def get_flags_for_scenario(scenario_name: ScenarioName) -> dict[str, str]:
    """Driver flags для scenario (используется в interpretation)."""
    return FLAGS_DECOMPOSITION.get(scenario_name, {})


__all__ = [
    "AS_OF_DATE",
    "REVIEW_AFTER_DAYS",
    "FORECAST_RANDOM_STATE",
    "CURRENT_STATE_2026_05",
    "ScenarioName",
    "SCENARIO_NAMES",
    "ScenarioParams",
    "PRESET_SCENARIOS",
    "OUParams",
    "OUForecast",
    "ASSET_PARAMS",
    "FLAGS_DECOMPOSITION",
    "compute_ou_forecast",
    "get_ou_params",
    "is_scenario_applicable",
    "parse_scenario",
    "scenario_label",
    "get_flags_for_scenario",
]
