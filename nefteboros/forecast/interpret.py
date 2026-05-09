"""Horizon-aware текстовая интерпретация прогноза для агента-аналитика.

Цель — детерминированный, воспроизводимый текст, который агент Сбера
вставляет в ответ пользователю.

ADR-0024: OU regime model. Interpretation описывает:
  - scenario semantics (base/bear/bull) с driver flags
  - OU calibration (long-run target μ, speed θ, vol σ, inflation drift)
  - mean reversion physics (термостат-аналогия)
  - structural property commodity (floor/ceiling)
  - snapshot freshness warning

См. ADR-0024 §«Решение».
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from nefteboros.forecast.registry import AssetMeta, get_asset
from nefteboros.forecast.scenarios import (
    AS_OF_DATE,
    REVIEW_AFTER_DAYS,
    get_flags_for_scenario,
)
from nefteboros.forecast.schema import (
    AssetGroup,
    DataSource,
    ForecastResult,
    Horizon,
    ModelMethod,
)


# =============================================================================
# Public API
# =============================================================================


def generate_interpretation(forecast: ForecastResult) -> str:
    """Сгенерировать многосекционный markdown-friendly текст для ForecastResult."""
    asset_meta = get_asset(forecast.asset)

    parts: list[str] = []

    # 1. Заголовок: что прогнозируется (включает scenario label)
    parts.append(_header(forecast, asset_meta))

    # 2. Сценарный блок: drivers + scenario summary
    scenario_block = _scenario_block(forecast)
    if scenario_block:
        parts.append(scenario_block)

    # 3. Точка + CI
    parts.append(_point_and_ci(forecast))

    # 4. OU calibration block (target, reversion speed, vol)
    parts.append(_ou_calibration_block(forecast))

    # 5. Horizon warning
    parts.append(_horizon_warning(forecast.horizon, asset_meta))

    # 6. Asset-specific qualifiers (derived / proxy / газ)
    qual = _asset_qualifier(asset_meta)
    if qual:
        parts.append(qual)

    # 7. Структурное свойство commodity (термостат)
    parts.append(_thermostat_block())

    # 8. Snapshot freshness warning
    snap_warn = _snapshot_freshness_warning(forecast.metadata)
    if snap_warn:
        parts.append(snap_warn)

    # 9. Citation hint
    parts.append(_citation_hint(forecast))

    return "\n\n".join(parts)


# =============================================================================
# Section builders
# =============================================================================


def _header(forecast: ForecastResult, meta: AssetMeta) -> str:
    asset_name = meta.display_name
    horizon_text = {
        Horizon.M1: "1 месяц",
        Horizon.M3: "3 месяца",
        Horizon.M6: "6 месяцев",
        Horizon.M12: "12 месяцев",
    }[forecast.horizon]

    scenario_label = forecast.metadata.get("scenario_label", "base")
    scenario_human = {
        "base": "**base** (текущее shock equilibrium)",
        "bear": "**bear** (de-escalation, return к pre-shock norm)",
        "bull": "**bull** (escalation, supply tightening)",
    }.get(scenario_label, f"**{scenario_label}**")

    return (
        f"**{asset_name}, прогноз на {horizon_text}.** "
        f"Сценарий: {scenario_human}. "
        f"Метод: regime-conditioned mean-reverting OU (см. ADR-0024)."
    )


def _scenario_block(forecast: ForecastResult) -> Optional[str]:
    """Driver flags + scenario summary."""
    scenario_label = forecast.metadata.get("scenario_label", "base")
    flags = get_flags_for_scenario(scenario_label)
    if not flags:
        return None

    lines = [f"**Driver flags ({scenario_label}):**"]
    for key, value in flags.items():
        if key == "summary":
            continue
        lines.append(f"  • **{key}**: {value}")

    summary = flags.get("summary", "")
    if summary:
        lines.append(f"\n_{summary}_")

    return "\n".join(lines)


def _point_and_ci(forecast: ForecastResult) -> str:
    end = forecast.points[-1]
    target_date = end.date.strftime("%Y-%m-%d")
    unit = _unit_label(forecast.asset)
    return (
        f"**Прогноз на {target_date}:** центр **{end.value:.2f}** {unit}. "
        f"CI 80%: [{end.ci_80.low:.2f}, {end.ci_80.high:.2f}]; "
        f"CI 95%: [{end.ci_95.low:.2f}, {end.ci_95.high:.2f}]."
    )


def _ou_calibration_block(forecast: ForecastResult) -> str:
    """OU параметры для diagnostic/transparency."""
    md = forecast.metadata
    mu_0 = md.get("ou_mu_0")
    mu_t = md.get("ou_mu_t")
    theta = md.get("ou_theta")
    sigma = md.get("ou_sigma")
    infl = md.get("ou_inflation")

    if mu_0 is None or theta is None:
        return ""

    half_life_months = math.log(2) / theta * 12 if theta else float("inf")
    unit = _unit_label(forecast.asset)

    lines = [
        "**OU калибровка** (mean reversion to scenario equilibrium):",
        f"  • **Long-run target μ₀** = {mu_0:.2f} {unit} (snapshot 2026-05-08); "
        f"μ(t) дрейфует с инфляцией {infl*100:.0f}%/y → μ(target) = {mu_t:.2f}",
        f"  • **Speed of reversion θ** = {theta:g}/year "
        f"(half-life {half_life_months:.1f} мес — насколько быстро рынок 'договаривается' к target)",
        f"  • **Volatility σ** = {sigma*100:.0f}%/year (annualized, regime-specific)",
    ]
    return "\n".join(lines)


def _horizon_warning(horizon: Horizon, meta: AssetMeta) -> str:
    if horizon == Horizon.M1:
        return (
            "На горизонте 1 месяц mean reversion almost отсутствует — current "
            "state holds. CI узкий, отражает только short-term volatility."
        )
    if horizon == Horizon.M3:
        return (
            "Горизонт 3 месяца — mean reversion начинает проявляться. CI bounded "
            "OU процессом, не расходится √h. **Структурные шоки** (новый ОПЕК+ "
            "raid, Hormuz reopens/closes, MOU подписан) — outside scenario "
            "framework, дополнить RAG/web (ADR-0013)."
        )
    if horizon == Horizon.M6:
        return (
            "Горизонт 6 месяцев — reversion сила доминирует. Mid близок к "
            "long-run target μ scenario. CI bounded, не расходится. "
            "**Обязательно дополнить** RAG-сценариями (WOO 2025, IEA Oil 2025) "
            "и свежими событиями через web-search."
        )
    return (
        "Горизонт 12 месяцев — mid почти равен μ(t) scenario (mean reversion "
        "почти полностью реализована). CI **bounded** OU процессом — это "
        "ключевое преимущество over GBM (где CI расходится √h). "
        "**Обязательно** обратиться к сценарным RAG-источникам: OPEC WOO 2025, "
        "IEA Oil 2025, ИНЭИ Прогноз 2024."
    )


def _asset_qualifier(meta: AssetMeta) -> Optional[str]:
    """Disclaimer для derived/proxy/газ активов."""
    if meta.primary_source == DataSource.DERIVED:
        if meta.spread_against == "brent":
            return (
                f"⚠️ **{meta.display_name}** — derived от Brent через режимный "
                f"spread. В OU framework derived assets имеют свою отдельную "
                f"calibration μ/θ/σ (см. scenarios.py ASSET_PARAMS), а не просто "
                f"Brent − spread. Это даёт чище CI."
            )
        if meta.derived_from:
            return (
                f"⚠️ **{meta.display_name}** — piecewise blend по Минфин-формуле "
                f"НДПИ. До 2025-01: blend = Urals (1.0). С 2025-01: 0.78 × Urals + "
                f"0.22 × ESPO. OU calibration на blend как whole."
            )

    if meta.group == AssetGroup.RUSSIAN_ENERGY_PROXY:
        return (
            f"⚠️ **{meta.display_name}** — это **финансовый proxy** российского "
            f"нефтегазового сектора. **INVERTED bull**: на escalation equity "
            f"падает (sanctions tighten, capital outflow), несмотря на "
            f"улучшение fundamentals газовых компаний. Empirically Q1 2022: "
            f"GAZP −50% YTD при Brent +50%. Russia-specific factors доминируют."
        )

    if meta.log_transform and meta.group == AssetGroup.GAS_GLOBAL:
        return (
            f"⚠️ Газовые ряды — inherently более волатильны чем нефть "
            f"(HH 2022 = 91%, TTF 2022 — extreme war shock €300+). "
            f"OU σ scenario-specific, slower θ — gas markets less liquid, "
            f"regime persists longer."
        )

    return None


def _thermostat_block() -> str:
    """Структурное обоснование mean reversion (термостат + commodity property)."""
    return (
        "**Почему mean reversion корректно для commodity** (см. ADR-0024). "
        "Термостат-аналогия: батарея тянет температуру к 22°C (μ); сила батареи "
        "определяет скорость восстановления (θ); сквозняки — random shocks (σ). "
        "Commodity имеет structural floor (cost of production) и ceiling "
        "(demand destruction) — цена **не уходит** в бесконечность. "
        "Исторически: 1985 Saudi flood, 1990 Gulf War, 2008 spike, 2014 OPEC "
        "flood, 2020 COVID, 2022 Russia war — все возвращались к long-run mean "
        "за 6-24 месяцев. Текущий Hormuz shock — тот же паттерн."
    )


def _snapshot_freshness_warning(metadata: dict) -> Optional[str]:
    as_of_str = metadata.get("scenario_as_of")
    if not as_of_str:
        return None
    try:
        as_of = date.fromisoformat(as_of_str)
    except ValueError:
        return None
    today = date.today()
    days_old = (today - as_of).days
    if days_old <= REVIEW_AFTER_DAYS:
        return None
    return (
        f"⚠️ **Snapshot устарел.** OU calibration привязана к состоянию рынка "
        f"на {as_of_str}; runtime — {today.isoformat()} (через {days_old} дней). "
        f"При крупных событиях с тех пор (MOU подписан/отменён, Hormuz reopens/"
        f"closes, новый ОПЕК+ raid) — μ/θ/σ могут быть некорректны. "
        f"См. ADR-0024 §«когда обновлять snapshot»."
    )


def _citation_hint(forecast: ForecastResult) -> str:
    # Single source of truth — ModelMethod enum value (ADR-0024 + A4 fix).
    method_label = forecast.method.value
    scenario = forecast.metadata.get("scenario_label", "base")
    return (
        f"_Цитировать как:_ `[Forecast: {method_label}, scenario={scenario}, CI 80%]` "
        f"(или `CI 95%` / `CI 80/95%` если оба уровня в ответе)."
    )


def _unit_label(asset_id: str) -> str:
    meta = get_asset(asset_id)
    return meta.unit


__all__ = ["generate_interpretation"]
