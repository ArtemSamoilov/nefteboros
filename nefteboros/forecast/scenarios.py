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
from collections.abc import Mapping
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
        # source sigma: pre_war 2021 ~22%, war_shock ~55%, cap_phase ~28%, OVX current ~70%
        # A8 recalibration: bear σ 0.20→0.25 для better 12m coverage на mixed history
        # (backtest showed 0.25 cov на 12m с σ=0.20; goal ~0.5 cov)
        "bear": OUParams(mu_0=70.0,  theta=3.0, sigma=0.25, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=98.0,  theta=2.0, sigma=0.25, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=120.0, theta=1.5, sigma=0.30, inflation=_OIL_INFLATION),
    },
    "wti": {
        # WTI ~ Brent − $5 typical premium; same volatility regime (A8: bear 0.20→0.25)
        "bear": OUParams(mu_0=66.0,  theta=3.0, sigma=0.25, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=94.0,  theta=2.0, sigma=0.25, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=115.0, theta=1.5, sigma=0.30, inflation=_OIL_INFLATION),
    },
    "urals": {
        # Urals = Brent − sanction-discount per scenario:
        # bear: Brent$70 − bear discount $8 = $62
        # base: Brent$98 − base discount $17 (cap_phase_2) = $81
        # bull: Brent$120 − bull discount $25 = $95
        # +sigma adjustment +2pp для spread variability
        # A8: bear σ 0.22→0.27 (consistent +5pp bump к oil bear)
        "bear": OUParams(mu_0=62.0, theta=3.0, sigma=0.27, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=81.0, theta=2.0, sigma=0.27, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=95.0, theta=1.5, sigma=0.32, inflation=_OIL_INFLATION),
    },
    "espo": {
        # ESPO ~ Brent − $5 typical (Asian premium pre-war, normalize sanctions)
        # A8: bear σ 0.21→0.26
        "bear": OUParams(mu_0=65.0,  theta=3.0, sigma=0.26, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=92.0,  theta=2.0, sigma=0.26, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=113.0, theta=1.5, sigma=0.31, inflation=_OIL_INFLATION),
    },
    "urals_minfin_blend": {
        # 0.78 × urals + 0.22 × espo (Минфин НДПИ-формула с 2025-01)
        # A8: bear σ 0.22→0.27
        "bear": OUParams(mu_0=63.0, theta=3.0, sigma=0.27, inflation=_OIL_INFLATION),
        "base": OUParams(mu_0=83.0, theta=2.0, sigma=0.27, inflation=_OIL_INFLATION),
        "bull": OUParams(mu_0=99.0, theta=1.5, sigma=0.32, inflation=_OIL_INFLATION),
    },

    # ----- Газ -----
    # source: HH 2022 = 91% real vol, 2023 = 69%; TTF 2022 extreme. Газ inherently
    # more volatile, slower mean reversion (less liquid markets, regime persists).
    # Inflation 4%/y — gas substitutable (electric heating, renewables) → lower passthrough
    # A8 recalibration: bear σ 0.35→0.45 (HH bear had 0.25 cov на 6m+;
    # газ spikes 2022 outside CI; goal — bear CI частично покрывает spikes).
    # Constraint: bear σ ≤ base σ (semantic: bear=calm regime, vol ≤ base shock).
    "henry_hub": {
        "bear": OUParams(mu_0=2.30, theta=2.0, sigma=0.45, inflation=0.04),
        "base": OUParams(mu_0=2.77, theta=1.5, sigma=0.45, inflation=0.04),
        "bull": OUParams(mu_0=3.50, theta=1.0, sigma=0.55, inflation=0.04),
    },
    "ttf": {
        # A8: bear σ 0.35→0.45 (= base, не inversion)
        "bear": OUParams(mu_0=35.0, theta=2.0, sigma=0.45, inflation=0.04),
        "base": OUParams(mu_0=43.0, theta=1.5, sigma=0.45, inflation=0.04),
        "bull": OUParams(mu_0=55.0, theta=1.0, sigma=0.50, inflation=0.04),
    },

    # ----- Российский нефтегаз proxy (INVERTED bull — escalation hurts equity) -----
    # source: Q1 2022 GAZP nominal 330→132 RUB (-60%) за 3 мес, потом slow recovery
    # к 165 (Aug 2022). Russia-specific factors (sanctions, RUB outflow, foreign
    # capital exit) доминируют над commodity tailwind.
    #
    # Inflation scenario-specific (A6 recalibration, ADR-0024 §«Trade-offs» №4):
    # - bear/base: 10%/y — CBR rate + страновая премия в стандартном режиме
    # - bull: 3%/y — RUB девальвируется в hard currency на escalation;
    #   equity nominal не получает CPI lift (FX dynamic доминирует)
    # μ_bull откалиброван к -25..-40% от spot для match 2022 panic depth.
    "moexog": {
        "bear": OUParams(mu_0=7200.0, theta=2.0, sigma=0.18, inflation=0.10),
        "base": OUParams(mu_0=6700.0, theta=1.5, sigma=0.22, inflation=0.10),
        # bull: μ -33% от spot (-26% effective at 12m)
        "bull": OUParams(mu_0=3800.0, theta=1.0, sigma=0.32, inflation=0.03),
    },
    "gazp": {
        "bear": OUParams(mu_0=130.0, theta=2.0, sigma=0.20, inflation=0.10),
        "base": OUParams(mu_0=117.0, theta=1.5, sigma=0.25, inflation=0.10),
        # bull: μ -49% от spot (-29% effective at 12m), match 2022 panic
        "bull": OUParams(mu_0=60.0,  theta=1.0, sigma=0.40, inflation=0.03),
    },
    "nvtk": {
        "bear": OUParams(mu_0=1280.0, theta=2.0, sigma=0.22, inflation=0.10),
        "base": OUParams(mu_0=1124.0, theta=1.5, sigma=0.27, inflation=0.10),
        # bull: μ -47% от spot (-28% effective at 12m)
        "bull": OUParams(mu_0=600.0,  theta=1.0, sigma=0.45, inflation=0.03),
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

    # Variance bounded: σ²/(2θ) × (1 - exp(-2θt)).
    # ADR-0024 §A7: sigma_dollar = σ × mid (не σ × spot). Это академически
    # корректнее когда mid дрейфует далеко от spot (bear/bull на длинных
    # horizons). Sensitivity test в tests/test_ou_sigma_anchor.py показал что
    # на extreme bear (spot=$100, μ=$70, 12m) разница в ширине CI ~30% между
    # σ×spot vs σ×mid; на base (mid≈spot) разница <2%.
    # mid in OU не зависит от σ (deterministic от θ, μ_0, S_0), потому formula
    # не recursive.
    sigma_dollar = params.sigma * abs(mid)
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


# =============================================================================
# Flag-driven μ recomputation — детерминированная цепочка (ADR-0025)
# =============================================================================
# Делает геополитические флаги РЕАЛЬНЫМ детерминированным входом μ (а не только
# текстом FLAGS_DECOMPOSITION). Цепочка:
#
#   состояния флагов → Σ Δmbpd (DRIVERS) → × (−$12/bbl Kilian) → μ_brent
#   производные нефти (wti/urals/espo/blend) → аффинно от μ_brent
#
# μ считает ДЕТЕРМИНИРОВАННАЯ формула из экспертной таблицы — LLM здесь НЕ
# участвует (классификация состояний флагов — отдельный этап/воркер).
#
# base — особый случай: anchored к текущему equilibrium/споту (μ_base из
# ASSET_PARAMS), НЕ «calm − дельты» (Σ=0 ⇒ возврат замороженной μ_base).
#
# Полная карта решений, восстановление baseline и разрешение противоречия
# калибровки ADR-0024 ($89 vs $104 baseline) — docs/adr/0025-flags-to-mu.md.

KILIAN_USD_PER_MBPD: float = 12.0
"""Эластичность цены нефти к устойчивому supply-сдвигу (Kilian 2009).

# source: ADR-0023 §Q2 коридор $10–15/bbl per 1 mbpd; Goldman severe implicit
# $12–13 ($115 vs $90 при +2 mbpd persistent loss); ADR-0024 §«Mapping flags».
"""

CALM_BASELINE_BRENT: float = 89.2
"""Calm-baseline μ Brent (USD/bbl) — якорь цепочки для bear/bull.

# source: ADR-0024 §«Mapping flags»: μ_bear = baseline − Σ·$12. Точное значение
# = μ_bear(70) + 1.6·12 = 89.2 (ADR/ТЗ округляют до $89). Это фитируемый якорь
# под сходимость bear, НЕ независимая «calm цена» (bear $70 ниже неё). base сюда
# НЕ входит (anchored отдельно). См. ADR-0025 §«Восстановление baseline».
"""

OIL_ASSETS: frozenset[str] = frozenset(
    {"brent", "wti", "urals", "espo", "urals_minfin_blend"}
)
"""Активы с flag-driven μ — ТОЛЬКО нефть (v1). Газ (henry_hub/ttf) и equity
(moexog/gazp/nvtk) сохраняют ручную калибровку ASSET_PARAMS: у них другая
driver-логика (см. ADR-0025 §Non-goals)."""


# Δmbpd к глобальному supply-demand БАЛАНСУ нефти относительно текущего/base
# состояния. Знак: + = профицит (цена вниз), − = дефицит (цена вверх). Базовое
# (current) состояние каждого драйвера = 0 (точка отсчёта). Каждое значение с
# # source; при новых данных — правка в одном месте.
DRIVERS: dict[str, dict[str, float]] = {
    "hormuz": {
        "blocked": 0.0,            # current/base reference   # source: ADR-0023 §Q2
        "partial_reopen": +1.5,    # source: ADR-0023 §Q2
        "full_reopen": +3.0,       # source: ADR-0023 §Q2
        "partial_closure": -3.27,  # калибровано под сходимость μ_bull=$120 (ADR-0025):
                                   # между −2 прозы и −5 таблицы ADR-0024 §Mapping (внутр. противоречие ADR)
        "full_closure": -5.0,      # source: ADR-0023 §Q2 (what-if, не в bull-пресете)
    },
    "iran": {
        "maximum_pressure_active": 0.0,  # current  # source: ADR-0023 §Q2
        "partial_lift": +0.6,            # source: ADR-0023 §Q2
        "full_lift": +1.2,               # source: ADR-0023 §Q2
        "further_tightening": -0.2,      # source: ADR-0023 §Q2
    },
    "opec_plus": {
        "gradual": 0.0,        # current (+206k bpd/мес unwind)  # source: ADR-0023 §Q2
        "accelerated": +0.5,   # source: scenarios.FLAGS_DECOMPOSITION bull «accelerated +0.5»
        "extended": -0.5,      # re-tighten cuts, защита цен  # source: ADR-0023 §Q2
    },
    "russia_cap": {
        "active": 0.0,              # current ($47.60/$44.10)  # source: ADR-0023 §Q2
        "tightened_dynamic": 0.0,   # price effect, НЕ volume   # source: ADR-0023 §Q2
        "removed": +0.5,            # Russian export normalizes  # source: ADR-0023 §Q2
    },
    "china_demand": {
        "base": 0.0,    # current (+0.2 mbpd y/y per IEA)  # source: ADR-0023 §Q2
        "weak": +0.4,   # −0.4 mbpd DEMAND ⇒ +0.4 к балансу (профицит)  # source: ADR-0023 §Q2 (demand→balance sign-flip)
        "strong": -0.4, # +0.4 mbpd demand ⇒ дефицит  # source: ADR-0023 §Q2
    },
}

# Текущее (base) состояние каждого драйвера — точка отсчёта Σ Δmbpd.
DRIVER_BASE_STATES: dict[str, str] = {
    "hormuz": "blocked",
    "iran": "maximum_pressure_active",
    "opec_plus": "gradual",
    "russia_cap": "active",
    "china_demand": "base",
}

# Преднастроенные наборы флагов, соответствующие ASSET_PARAMS scenarios.
# base = current shock; bear = de-escalation (+1.6 mbpd); bull = escalation (−2.57).
FLAG_PRESETS: dict[ScenarioName, dict[str, str]] = {
    "base": dict(DRIVER_BASE_STATES),
    "bear": {
        "hormuz": "partial_reopen",
        "iran": "partial_lift",
        "opec_plus": "extended",
        "russia_cap": "active",
        "china_demand": "base",
    },
    "bull": {
        "hormuz": "partial_closure",
        "iran": "further_tightening",
        "opec_plus": "accelerated",
        "russia_cap": "tightened_dynamic",
        "china_demand": "weak",
    },
}

# Производные нефти: μ_asset = α·μ_brent + β (аффинно к chain-Brent). α,β
# подогнаны под (bear, bull) замороженные μ → bear точно, bull ≤$0.05. Физика:
# α<1 у urals/blend = санкционный дисконт ширится с ценой Brent ($8→$25). base
# — anchored (особый случай), в аффинной карте не участвует.
# # source: ASSET_PARAMS + spread-комментарии в ASSET_PARAMS (urals/espo/blend).
_DERIVED_OIL_AFFINE: dict[str, tuple[float, float]] = {
    "wti": (0.98, -2.6),                  # bear 70→66, bull 120→115 (~$5 premium)
    "urals": (0.66, 15.8),                # bear 70→62, bull 120→95 (дисконт 8→25)
    "espo": (0.96, -2.2),                 # bear 70→65, bull 120→113
    "urals_minfin_blend": (0.72, 12.6),   # bear 70→63, bull 120→99 (≈0.78·urals+0.22·espo)
}


def supply_balance_from_flags(flag_states: Mapping[str, str]) -> float:
    """Σ Δmbpd по флагам относительно текущего/base состояния (+ = профицит).

    Неуказанные драйверы трактуются в base-состоянии (Δ=0).

    Raises:
        ValueError: неизвестный driver или state.
    """
    total = 0.0
    for driver, state in flag_states.items():
        if driver not in DRIVERS:
            raise ValueError(
                f"Unknown driver {driver!r}. Valid: {sorted(DRIVERS)}."
            )
        table = DRIVERS[driver]
        if state not in table:
            raise ValueError(
                f"Unknown state {state!r} for driver {driver!r}. "
                f"Valid: {sorted(table)}."
            )
        total += table[state]
    return total


def compute_mu_from_flags(asset: str, flag_states: Mapping[str, str]) -> float:
    """Детерминированный пересчёт μ (long-run target) из состояний флагов.

    Цепочка (ADR-0025): Σ Δmbpd (DRIVERS) → × (−Kilian) → μ_brent; производные
    нефти аффинно от μ_brent. base (Σ=0) — anchored к ASSET_PARAMS (особый
    случай, «не calm − дельты»).

    Args:
        asset: один из OIL_ASSETS (ТОЛЬКО нефть в v1).
        flag_states: {driver: state}; неуказанные драйверы = base-состояние.

    Returns:
        μ_0 (USD/bbl) для (asset, flag_states).

    Raises:
        ValueError: asset не нефтяной, либо неизвестный driver/state.
    """
    if asset not in OIL_ASSETS:
        raise ValueError(
            f"compute_mu_from_flags поддерживает только нефть {sorted(OIL_ASSETS)}; "
            f"got {asset!r}. Газ/equity сохраняют ручную калибровку (ADR-0024)."
        )
    balance = supply_balance_from_flags(flag_states)
    # base equilibrium — anchored (особый случай, не через формулу):
    if balance == 0.0:
        return ASSET_PARAMS[asset]["base"].mu_0
    mu_brent = CALM_BASELINE_BRENT - KILIAN_USD_PER_MBPD * balance
    if asset == "brent":
        return mu_brent
    alpha, beta = _DERIVED_OIL_AFFINE[asset]
    return alpha * mu_brent + beta


def ou_params_with_flag_mu(
    asset: str,
    scenario: ScenarioName,
    flag_states: Mapping[str, str],
) -> OUParams:
    """OUParams со θ/σ/infl из (asset, scenario) пресета и μ_0, пересчитанной из флагов.

    v1: флаги двигают ТОЛЬКО μ (long-run target). θ/σ/inflation берутся из
    scenario-пресета без изменений (non-goal: калибровка θ/σ — см. ADR-0025).
    """
    preset = get_ou_params(asset, scenario)
    return OUParams(
        mu_0=compute_mu_from_flags(asset, flag_states),
        theta=preset.theta,
        sigma=preset.sigma,
        inflation=preset.inflation,
    )


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
    # flag-driven μ chain (ADR-0025)
    "KILIAN_USD_PER_MBPD",
    "CALM_BASELINE_BRENT",
    "OIL_ASSETS",
    "DRIVERS",
    "DRIVER_BASE_STATES",
    "FLAG_PRESETS",
    "supply_balance_from_flags",
    "compute_mu_from_flags",
    "ou_params_with_flag_mu",
]
