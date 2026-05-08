"""Horizon-aware текстовая интерпретация прогноза для агента-аналитика.

Цель — детерминированный, воспроизводимый текст, который агент Сбера
вставляет в ответ пользователю. Без LLM-обогащения (в PR1) — это даёт:

  - Воспроизводимость (одни и те же числа → одинаковый текст).
  - Тестируемость в unit-тестах.
  - Никаких галлюцинаций о цифрах.

Формат текста зависит от:
  - horizon (1m/3m/6m/12m) — масштаб неопределённости.
  - asset group + derived/proxy качество — что мы реально предсказываем.
  - method — какой инструмент.
  - метрики (если приложен backtest_summary) — оценка качества модели.
  - **scenario** (v2.1, ADR-0023) — base / bear / bull / custom; драйверы;
    base anchor shift (transparent observation-anchored корректировка).

См. ADR-0012, ADR-0023.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from nefteboros.forecast.registry import AssetMeta, get_asset
from nefteboros.forecast.scenarios import AS_OF_DATE, REVIEW_AFTER_DAYS
from nefteboros.forecast.schema import (
    AssetGroup,
    BacktestSummary,
    DataSource,
    ForecastResult,
    Horizon,
    ModelMethod,
)


# =============================================================================
# Public API
# =============================================================================


def generate_interpretation(forecast: ForecastResult) -> str:
    """Сгенерировать текст-интерпретацию для ForecastResult.

    Возвращает многострочный markdown-friendly текст с:
      - центральной оценкой и CI (80/95)
      - оценкой качества (если backtest_summary есть)
      - horizon-специфичными предупреждениями
      - указаниями на сценарные источники для дальних горизонтов
      - disclaimers для derived/proxy активов
    """
    asset_meta = get_asset(forecast.asset)
    end = forecast.points[-1]

    parts: list[str] = []

    # 1. Заголовок: что прогнозируется (включает scenario label)
    parts.append(_header(forecast, asset_meta))

    # 2. Сценарный блок: drivers, anchor disclosure (если применимо)
    scenario_block = _scenario_block(forecast)
    if scenario_block:
        parts.append(scenario_block)

    # 3. Точка + CI
    parts.append(_point_and_ci(forecast))

    # 4. Метрики качества (если есть)
    if forecast.backtest_summary is not None:
        parts.append(_quality_block(forecast.backtest_summary))

    # 5. Horizon-warning
    parts.append(_horizon_warning(forecast.horizon, asset_meta))

    # 6. Asset-specific qualifiers (derived / univariate proxy / gas)
    qual = _asset_qualifier(asset_meta, forecast.method)
    if qual:
        parts.append(qual)

    # 7. Информация о свежести данных
    if "data_last_observation" in forecast.metadata:
        parts.append(
            f"Последняя наблюдаемая цена — на {forecast.metadata['data_last_observation']}."
        )

    # 8. Snapshot freshness (ADR-0023)
    snapshot_warn = _snapshot_freshness_warning(forecast.metadata)
    if snapshot_warn:
        parts.append(snapshot_warn)

    # 9. Универсальное предупреждение про политическую волатильность
    parts.append(
        "⚠️ **Heavy-tail политическая волатильность.** Активы нефть/газ — "
        "страшно зависят от геополитики (war 2022, Iran 2026, OPEC+ решения, "
        "санкционные шоки). Любая time-series модель — **base-case в спокойном "
        "режиме**, не предсказатель структурных шоков. Финальный диапазон цен "
        "должен формироваться **гибридно**: модель + RAG-сценарии + web-новости "
        "(см. ADR-0013)."
    )

    # 10. Citation hint для агента (ADR-0023 §Q4)
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
    method_text = {
        ModelMethod.RANDOM_WALK: "End-of-month Random Walk (honest baseline)",
        ModelMethod.SARIMAX: "SARIMAX",
        ModelMethod.XGBOOST: "Gradient Boosting (sklearn GBR в PR1; XGBoost в PR3)",
        ModelMethod.ENSEMBLE: "Ensemble (среднее SARIMAX + GBR)",
    }[forecast.method]
    scenario = forecast.metadata.get("scenario_label", "base")
    scenario_label_human = {
        "base": "base (текущий shock-режим)",
        "bear": "bear (de-escalation)",
        "bull": "bull (escalation)",
        "custom": "custom (произвольная комбинация драйверов)",
    }.get(scenario, scenario)

    return (
        f"**{asset_name}, прогноз на {horizon_text}.** "
        f"Метод: {method_text}. Сценарий: **{scenario_label_human}**."
    )


def _scenario_block(forecast: ForecastResult) -> Optional[str]:
    """Сценарный блок: anchor disclosure + drivers + scenario delta.

    Прозрачен про observation-anchored shift (см. ADR-0023 §Q1):
    модель не схватывает shock; base anchored к spot; bear/bull = base + delta.

    Возвращает None — если scenario_applicable=False (нет смысла рассказывать
    про драйверы, скажем, для TTF в v2.1).
    """
    md = forecast.metadata
    if not md.get("scenario_applicable", False):
        # Asset вне applicability — отдельная нотификация ниже
        if "scenario_applicable" in md:
            return (
                "ℹ️ Сценарии (bear/base/bull) для этого актива в v2.1 не применяются "
                "(см. ADR-0023). Возвращён model output без shift-калибровки. "
                "В v2.2+ — расширение на газ и российские акции."
            )
        return None

    raw_target = md.get("raw_model_target_value")
    anchor_shift = md.get("base_anchor_shift", 0.0)
    delta_low = md.get("scenario_delta_low", 0.0)
    delta_mid = md.get("scenario_delta_mid", 0.0)
    delta_high = md.get("scenario_delta_high", 0.0)
    breakdown: dict[str, Any] = md.get("scenario_driver_breakdown", {})
    scenario_label = md.get("scenario_label", "base")
    scenario_params: dict[str, Any] = md.get("scenario_params", {})

    lines: list[str] = []

    # Anchor disclosure
    if raw_target is not None and abs(anchor_shift) > 0.5:
        unit = _unit_label(forecast.asset)
        lines.append(
            f"**Базовая корректировка (observation-anchored).** Модель ансамбля "
            f"(5y train) выдала {raw_target:.2f} {unit} на конец горизонта. "
            f"Текущий spot отличается — применён прозрачный shift "
            f"**{anchor_shift:+.2f} {unit}** для приведения к base scenario "
            f"(текущее состояние рынка). Это **observation, не модель**: при "
            f"изменении spot на ±$10 — все сценарии сдвинутся на ±$10."
        )

    # Scenario delta
    if scenario_label != "base":
        unit = _unit_label(forecast.asset)
        lines.append(
            f"**Сценарный сдвиг ({scenario_label}).** Изменение от base: "
            f"**{delta_mid:+.2f} {unit}** (диапазон {delta_low:+.2f}..{delta_high:+.2f})."
        )

    # Driver breakdown
    if breakdown:
        driver_lines = []
        for driver_name, (low, mid, high) in breakdown.items():
            if abs(mid) < 0.5 and abs(low) < 0.5 and abs(high) < 0.5:
                continue  # пропускаем нулевые драйверы
            state = scenario_params.get(driver_name, "?")
            driver_lines.append(
                f"  • **{driver_name}** ({state}): {mid:+.1f} (диапазон {low:+.1f}..{high:+.1f})"
            )
        if driver_lines:
            lines.append("**Вклад драйверов** ($/bbl):\n" + "\n".join(driver_lines))

    if not lines:
        return None
    return "\n\n".join(lines)


def _snapshot_freshness_warning(metadata: dict) -> Optional[str]:
    """Warning если snapshot 2026-05-08 устарел (>14 дней до runtime).

    См. ADR-0023 §«valid_through убрал».
    """
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
        f"⚠️ **Snapshot устарел.** Калибровка сценариев привязана к состоянию "
        f"рынка на {as_of_str}; runtime — {today.isoformat()} (через {days_old} "
        f"дней). При крупных событиях с тех пор (MOU подписан/отменён, Hormuz "
        f"reopens/closes, новый ОПЕК+ raid) — shifts могут быть некорректны. "
        f"См. ADR-0023 §«когда обновлять snapshot»."
    )


def _citation_hint(forecast: ForecastResult) -> str:
    """Citation в формате ADR-0023 §Q4 для использования агентом.

    Формат: `[Forecast: <model>, scenario=<label>, CI <level>]`
    Mandatory scenario, mandatory level. Default `80%`.
    """
    method_label = {
        ModelMethod.RANDOM_WALK: "random_walk",
        ModelMethod.SARIMAX: "sarimax",
        ModelMethod.XGBOOST: "gbr",
        ModelMethod.ENSEMBLE: "ensemble",
    }[forecast.method]
    scenario = forecast.metadata.get("scenario_label", "base")
    return (
        f"_Цитировать как:_ `[Forecast: {method_label}, scenario={scenario}, CI 80%]` "
        f"(или `CI 95%` / `CI 80/95%` если оба уровня в ответе)."
    )


def _point_and_ci(forecast: ForecastResult) -> str:
    end = forecast.points[-1]
    target_date = end.date.strftime("%Y-%m-%d")
    return (
        f"Центральная оценка на {target_date}: **{end.value:.2f}** "
        f"({_unit_label(forecast.asset)}). "
        f"80% CI: [{end.ci_80.low:.2f}, {end.ci_80.high:.2f}]; "
        f"95% CI: [{end.ci_95.low:.2f}, {end.ci_95.high:.2f}]."
    )


def _quality_block(bt: BacktestSummary) -> str:
    """Текст про качество модели по результатам бектеста.

    Берём aggregate-метрики (или average по сегментам), даём оценку.
    """
    aggregate = next(
        (m for m in bt.per_regime if m.regime.value == "aggregate"),
        None,
    )
    if aggregate is None and bt.per_regime:
        # average across regimes (fallback)
        nums_mape = [m.mape for m in bt.per_regime if m.mape is not None]
        nums_mase = [m.mase_vs_rw for m in bt.per_regime if m.mase_vs_rw is not None]
        nums_cov80 = [m.coverage_80 for m in bt.per_regime if m.coverage_80 is not None]
        if nums_mape and nums_mase and nums_cov80:
            mape = sum(nums_mape) / len(nums_mape)
            mase = sum(nums_mase) / len(nums_mase)
            cov80 = sum(nums_cov80) / len(nums_cov80)
        else:
            return "Бектест: метрики не вычислены."
    elif aggregate is not None:
        mape = aggregate.mape or 0.0
        mase = aggregate.mase_vs_rw or 0.0
        cov80 = aggregate.coverage_80 or 0.0
    else:
        return "Бектест: метрики не вычислены."

    # Оценка
    if mase < 0.85:
        verdict = "модель **уверенно бьёт persistence (RW)**"
    elif mase < 1.0:
        verdict = "модель слегка обыгрывает persistence"
    elif mase < 1.15:
        verdict = "модель сравнима с persistence"
    else:
        verdict = "модель **хуже** persistence — добавленной ценности нет"

    cov_assessment = ""
    target_cov = 0.80
    if cov80 < target_cov - 0.10:
        cov_assessment = " CI **систематически слишком узок** (под-coverage)"
    elif cov80 > target_cov + 0.10:
        cov_assessment = " CI избыточно широк (over-coverage)"

    return (
        f"**Качество модели на бектесте** (walk-forward {bt.history_window_years:.0f}y): "
        f"MAPE = {mape:.1f}%, MASE против RW = {mase:.2f} ({verdict}), "
        f"эмпирическое 80% coverage = {cov80*100:.0f}%{cov_assessment}."
    )


def _horizon_warning(horizon: Horizon, meta: AssetMeta) -> str:
    if horizon == Horizon.M1:
        return (
            "На горизонте 1 месяц добавленная ценность модели над persistence "
            "обычно минимальна — нефть/газ на коротких сроках близки к "
            "mean-reverting шуму. Использовать прогноз с полным CI, не точку."
        )
    if horizon == Horizon.M3:
        return (
            "Горизонт 3 месяца — стандартный для tactical-аналитики; модель "
            "учитывает наблюдаемые экзогены (запасы, USD, futures curve). "
            "**Структурные шоки** (геополитика, OPEC+ решения, новые санкции) "
            "моделью не учитываются — нужно дополнить сценариями из RAG "
            "и свежими новостями (см. ADR-0013, hybrid forecasting)."
        )
    if horizon == Horizon.M6:
        return (
            "Горизонт 6 месяцев — структурные факторы доминируют. CI расширен. "
            "Прогноз информативен скорее как «диапазон сценариев», чем точка. "
            "**Обязательно дополнить** сценариями из RAG (WOO, IEA Oil, CRS) "
            "и свежими событиями через web-search."
        )
    # 12m
    return (
        "**Горизонт 12 месяцев — точечная оценка ненадёжна** (литература: "
        "Baumeister-Kilian, EIA STEO methodology). Для долгосрочных решений "
        "**обязательно** обратиться к сценарным прогнозам в RAG-корпусе: "
        "OPEC World Oil Outlook 2025, IEA Oil 2025, ИНЭИ Прогноз 2024, "
        "Энергостратегия РФ-2050. Также — web-search для свежих событий."
    )


def _asset_qualifier(meta: AssetMeta, method: ModelMethod) -> Optional[str]:
    """Дополнительный disclaimer для derived/univariate-proxy/gas активов."""
    if meta.primary_source == DataSource.DERIVED:
        if meta.spread_against == "brent":
            return (
                f"⚠️ **{meta.display_name}** — derived прогноз: "
                f"получен из Brent-прогноза вычитанием режимного спреда "
                f"(see spread_schedule.py — источники Bruegel WP 32/2025 + Минэк). "
                f"CI расширен на spread-uncertainty. Модель не обучается на "
                f"исторических Urals-сделках напрямую (открытых daily-данных за "
                f"полные 5 лет нет — investing.com обрывается в Feb 2025)."
            )
        if meta.derived_from:
            return (
                f"⚠️ **{meta.display_name}** — piecewise blend по официальной "
                f"Минфин-формуле НДПИ. До 2025-01: blend = Urals (1.0). "
                f"С 2025-01: blend = 0.78 × Urals + 0.22 × ESPO. "
                f"CI — convolution компонентных CI."
            )

    if meta.group == AssetGroup.RUSSIAN_ENERGY_PROXY:
        return (
            f"⚠️ **{meta.display_name}** — это **финансовый proxy** для "
            f"российского нефтегазового сектора (биржевая стоимость акций / "
            f"отраслевой индекс), не цена газа per se. Прямых daily-котировок "
            f"внутрироссийского газа в открытых источниках нет (СПбМТСБ за "
            f"платным каналом). Модель univariate — без нефтяных экзогенов "
            f"(они нерелевантны для акций; RU-macro экзогены — в PR3)."
        )

    if meta.log_transform and meta.group == AssetGroup.GAS_GLOBAL:
        return (
            f"⚠️ Газовые ряды (особенно TTF) имели экстремумы 2022 года ×10 от "
            f"нормы (war shock, EU storage panic). Модель работает на log(price), "
            f"но coverage CI на исторических шоковых периодах может проседать."
        )

    return None


def _unit_label(asset_id: str) -> str:
    meta = get_asset(asset_id)
    return meta.unit


__all__ = ["generate_interpretation"]
