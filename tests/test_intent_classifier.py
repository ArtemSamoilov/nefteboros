"""Unit-тесты для rule-based classify_intent + extract_horizon.

Покрывают правила #1, #3, #5 из ADR-0013 §«Constraints for SKILL.md».
Правила #2 (unknown asset proxy) и #4 (derived без method) — отложены
в integration PR'ах, тестов на них здесь нет (по дизайну minimal-graph).

См. ADR-0014 §«Аргументация — почему rule-based classify_intent».
"""

from __future__ import annotations

import pytest

from nefteboros.forecast.schema import Horizon
from nefteboros.graphs.intents import classify_intent, extract_horizon
from nefteboros.graphs.state import IntentType


# =============================================================================
# Empty / whitespace
# =============================================================================


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_empty_query_is_out_of_scope(query: str) -> None:
    intent = classify_intent(query)
    assert intent.type == IntentType.OUT_OF_SCOPE
    assert intent.matched_rule == "empty_query"


# =============================================================================
# Rule #5 — Russian gas direct → refusal
# =============================================================================


@pytest.mark.parametrize(
    "query",
    [
        "цена газа в России в рублях за тыс.м³",
        "Сколько российский газ стоит внутри страны?",
        "Прогноз цены газа в России на 2027",
        "внутрироссийский газ — какая цена",
        "за тыс м³ российского газа сейчас сколько рублей",
    ],
)
def test_rule_5_russian_gas_refusal(query: str) -> None:
    intent = classify_intent(query)
    assert intent.type == IntentType.RUSSIAN_GAS_REFUSAL
    assert intent.matched_rule == "rule_5_russian_gas"
    assert intent.refuse_reason and "TTF" in intent.refuse_reason


# =============================================================================
# Rule #3 — Horizon refusal (1d/1w / >=18m)
# =============================================================================


@pytest.mark.parametrize(
    "query",
    [
        "прогноз brent на завтра",
        "цена нефти на день",
        "WTI на сутки",
        "TTF на неделю",
        "henry hub за день",
    ],
)
def test_rule_3_short_horizon_is_out_of_scope(query: str) -> None:
    intent = classify_intent(query)
    assert intent.type == IntentType.OUT_OF_SCOPE
    assert intent.matched_rule == "rule_3_horizon"
    assert intent.refuse_reason and "Дей-трейдинг" in intent.refuse_reason


@pytest.mark.parametrize(
    "query",
    [
        "прогноз нефти на 24 месяца",
        "Brent на 2 года",
        "прогноз ttf на 36 месяцев",
        "henry hub на 5 лет",
    ],
)
def test_rule_3_long_horizon_is_out_of_scope(query: str) -> None:
    intent = classify_intent(query)
    assert intent.type == IntentType.OUT_OF_SCOPE
    assert intent.matched_rule == "rule_3_horizon"
    assert intent.refuse_reason and "сценари" in intent.refuse_reason


# =============================================================================
# Rule #1 — generic asset disambiguation (default branches)
# =============================================================================


def test_rule_1_oil_generic_default_brent() -> None:
    intent = classify_intent("прогноз нефти на квартал")
    assert intent.type == IntentType.FORECAST_SIMPLE
    assert intent.forecast_assets == ["brent"]
    assert intent.forecast_horizon == Horizon.M3
    assert intent.matched_rule == "rule_1_oil_default"


def test_rule_1_gas_generic_default_hh_plus_ttf() -> None:
    intent = classify_intent("прогноз газа на месяц")
    assert intent.type == IntentType.FORECAST_SIMPLE
    assert intent.forecast_assets == ["henry_hub", "ttf"]
    assert intent.forecast_horizon == Horizon.M1
    assert intent.matched_rule == "rule_1_gas_default"


def test_rule_1_wti_explicit() -> None:
    intent = classify_intent("прогноз WTI на полгода")
    assert intent.type == IntentType.FORECAST_SIMPLE
    assert intent.forecast_assets == ["wti"]
    assert intent.forecast_horizon == Horizon.M6
    assert intent.matched_rule == "rule_1_wti"


def test_rule_1_brent_explicit_no_ru_context() -> None:
    intent = classify_intent("прогноз Brent на 3 месяца")
    assert intent.type == IntentType.FORECAST_SIMPLE
    assert intent.forecast_assets == ["brent"]
    assert intent.forecast_horizon == Horizon.M3
    assert intent.matched_rule == "rule_1_brent_explicit"


def test_rule_1_ttf_explicit() -> None:
    intent = classify_intent("TTF на год")
    assert intent.type == IntentType.FORECAST_SIMPLE
    assert intent.forecast_assets == ["ttf"]
    assert intent.forecast_horizon == Horizon.M12
    assert intent.matched_rule == "rule_1_ttf"


def test_rule_1_henry_hub_explicit() -> None:
    intent = classify_intent("Henry Hub на квартал")
    assert intent.type == IntentType.FORECAST_SIMPLE
    assert intent.forecast_assets == ["henry_hub"]
    assert intent.forecast_horizon == Horizon.M3
    assert intent.matched_rule == "rule_1_henry_hub"


# =============================================================================
# Rule #1 РФ-контекст — параллельно brent + urals + urals_minfin_blend
# =============================================================================


@pytest.mark.parametrize(
    "query",
    [
        "сколько Минфин закладывает по нефти в бюджет 2026",
        "прогноз нефти для бюджета РФ на год",
        "российская нефть на квартал",
        "налоговая цена нефти на 6 месяцев",
        "нефтегаздоходы РФ — какой прогноз цен нефти на год",
    ],
)
def test_rule_1_oil_with_ru_context(query: str) -> None:
    intent = classify_intent(query)
    assert intent.type == IntentType.FORECAST_WITH_CONTEXT
    assert intent.forecast_assets == ["brent", "urals", "urals_minfin_blend"]
    assert intent.matched_rule == "rule_1_oil_ru_context"


def test_rule_1_urals_explicit_triggers_with_context() -> None:
    intent = classify_intent("Urals на 6 месяцев")
    assert intent.type == IntentType.FORECAST_WITH_CONTEXT
    assert intent.forecast_assets == ["brent", "urals", "urals_minfin_blend"]
    assert intent.forecast_horizon == Horizon.M6


# =============================================================================
# Out of scope (no keyword match)
# =============================================================================


@pytest.mark.parametrize(
    "query",
    [
        "погода в Москве",
        "напиши стихотворение про море",
        "сколько весит самолёт",
        "помоги выбрать ноутбук",
    ],
)
def test_no_keyword_match_is_out_of_scope(query: str) -> None:
    intent = classify_intent(query)
    assert intent.type == IntentType.OUT_OF_SCOPE
    assert intent.matched_rule == "no_keyword_match"


# =============================================================================
# extract_horizon — отдельные unit-тесты
# =============================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("на квартал", Horizon.M3),
        ("на полгода", Horizon.M6),
        ("на год", Horizon.M12),
        ("на 1 месяц", Horizon.M1),
        ("на 3 мес", Horizon.M3),
        ("на 6 месяцев", Horizon.M6),
        ("на 12 месяцев", Horizon.M12),
        ("на 1 год", Horizon.M12),
    ],
)
def test_extract_horizon_supported(raw: str, expected: Horizon) -> None:
    h, reason = extract_horizon(f"прогноз {raw}")
    assert reason is None
    assert h == expected


def test_extract_horizon_rounds_to_nearest_supported() -> None:
    """4 месяца → ближайший supported — 3. 8 → 6."""
    h_4, reason_4 = extract_horizon("прогноз на 4 месяца")
    assert reason_4 is None
    assert h_4 == Horizon.M3

    h_8, reason_8 = extract_horizon("прогноз на 8 месяцев")
    assert reason_8 is None
    assert h_8 == Horizon.M6


def test_extract_horizon_no_match_returns_none() -> None:
    h, reason = extract_horizon("прогноз нефти")
    assert h is None
    assert reason is None


@pytest.mark.parametrize("raw", ["на 18 месяцев", "на 24 месяца", "на 3 года", "на 5 лет"])
def test_extract_horizon_too_long_returns_reason(raw: str) -> None:
    h, reason = extract_horizon(f"прогноз {raw}")
    assert h is None
    assert reason is not None
    assert "сценари" in reason


@pytest.mark.parametrize("raw", ["на завтра", "на день", "на неделю", "на сутки"])
def test_extract_horizon_too_short_returns_reason(raw: str) -> None:
    h, reason = extract_horizon(f"прогноз {raw}")
    assert h is None
    assert reason is not None
    assert "Дей-трейдинг" in reason


# =============================================================================
# Edge: rule ordering — RU-gas pattern бьёт horizon trigger
# =============================================================================


def test_rule_ordering_russian_gas_beats_horizon() -> None:
    """Запрос содержит и russian_gas trigger, и daily/long horizon —
    rule #5 (russian_gas) идёт первым и выигрывает; #3 (horizon)
    не запускается."""
    intent = classify_intent("цена газа в России на 2 года в рублях за тыс.м³")
    assert intent.type == IntentType.RUSSIAN_GAS_REFUSAL
    assert intent.matched_rule == "rule_5_russian_gas"


def test_rule_ordering_horizon_beats_asset() -> None:
    """Запрос с asset keyword + invalid horizon — rule #3 выигрывает."""
    intent = classify_intent("прогноз WTI на 24 месяца")
    assert intent.type == IntentType.OUT_OF_SCOPE
    assert intent.matched_rule == "rule_3_horizon"


# =============================================================================
# Scenario detection — bear/base/bull triggers (Track A v2.1, ADR-0024)
# =============================================================================


@pytest.mark.parametrize(
    "query",
    [
        "Дай прогноз цены Brent на 3 месяца с разбивкой по сценариям bear/base/bull",
        "Стресс-тест Urals на 6 месяцев",
        "Дай оптимистичный сценарий по WTI",
        "Медвежий и бычий сценарий по brent",
        "пессимистичный прогноз газа TTF",
        "разбивка по сценариям прогноза brent",
    ],
)
def test_scenario_triggers_yield_three_scenarios(query: str) -> None:
    """Запросы с scenario-триггерами → forecast_scenarios=['bear','base','bull']
    (порядок важен: bear→base→bull для UX в synthesize)."""
    intent = classify_intent(query)
    assert intent.forecast_scenarios == ["bear", "base", "bull"]


@pytest.mark.parametrize(
    "query",
    [
        "Прогноз Brent на 3 месяца",
        "Какая цена нефти?",
        "Прогноз газа TTF",
        "Урал нефть прогноз",
    ],
)
def test_no_scenario_triggers_default_base_only(query: str) -> None:
    """Запросы без scenario-триггеров → ['base'] (default single-scenario).
    Backward-compat: forecast_call делает один вызов как раньше."""
    intent = classify_intent(query)
    assert intent.forecast_scenarios == ["base"]


def test_scenario_triggers_dont_change_intent_type() -> None:
    """Scenario-триггер не должен ломать routing — intent.type выбирается
    asset-правилами, scenarios — orthogonal dimension."""
    intent = classify_intent("Brent с разбивкой по сценариям bear/base/bull")
    assert intent.type == IntentType.FORECAST_SIMPLE
    assert intent.matched_rule == "rule_1_brent_explicit"
    assert intent.forecast_assets == ["brent"]
    assert intent.forecast_scenarios == ["bear", "base", "bull"]
