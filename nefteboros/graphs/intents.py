"""Rule-based classify_intent для analyst graph.

Реализует правила #1, #3, #5 из ADR-0013 §«Constraints for SKILL.md» как
regex/keyword matching. Правила #2 (unknown asset proxy) и #4 (derived без
method) — отложены в integration PR'ы; см. ADR-0014.

Алгоритм classify_intent — earliest-match-wins:

1. Russian gas direct (rule #5)            → russian_gas_refusal.
2. Horizon refuse-trigger (rule #3)        → out_of_scope (1d/1w/завтра, >=18m).
3. WTI keyword                              → forecast_simple [wti].
4. Brent-only (без РФ-контекста)            → forecast_simple [brent].
5. Urals OR oil + РФ-контекст               → forecast_with_context
                                              [brent, urals, urals_minfin_blend].
6. TTF                                       → forecast_simple [ttf].
7. Henry Hub                                 → forecast_simple [henry_hub].
8. Generic gas                               → forecast_simple [henry_hub, ttf].
9. Generic oil                               → forecast_simple [brent].
10. Else                                     → out_of_scope.

Не использует LLM — детерминирован, быстр, тестируем. Unit-тесты в
tests/test_intent_classifier.py покрывают каждое правило.
"""

from __future__ import annotations

import re
from typing import Optional

from nefteboros.forecast.schema import Horizon
from nefteboros.graphs.state import Intent, IntentType


# =============================================================================
# Keyword sets (case-insensitive)
# =============================================================================

# Rule #5: Russian gas direct pricing.
# Везде используем `газ\w{0,3}` (а не `газ\w*`), чтобы матчить склонения
# («газ», «газа», «газом», «газами») и НЕ цеплять «Газпром» / «газификация».
_RUSSIAN_GAS_PATTERNS = [
    # «цена/цены [на] российский газ» / «цена рф газ»
    r"\bцен[ауые]\s+(?:на\s+)?(?:российск\w+\s+|рф\s+)газ\w{0,3}\b",
    # «российский|рф газ … (в рублях|за тыс|тыс.м³|внутри)»
    r"\b(?:российск\w+|рф)\s+газ\w{0,3}\b.*\b(?:в\s+рубл|за\s+тыс|тыс\.?\s*м[³3]|внутр\w*)\b",
    # «внутрироссийский газ»
    r"\bвнутрироссийск\w+\s+газ\w{0,3}\b",
    # «газ(а/у/ом) в России»
    r"\bгаз\w{0,3}\s+в\s+росси\w+\b",
    # «тыс.м³» / «1000 м³» — однозначный indicator РФ-внутреннего газа
    r"\b(?:тыс\.?|1000)\s*м[³3]\b",
    # «рублей за тыс/м³» — financial signal внутреннего газа
    r"\b(?:рубл\w*|руб\.?)\s*(?:за|/)\s*(?:тыс|1000|м[³3])\b",
]

# Rule #1 РФ-контекст триггеры
_RU_CONTEXT_KEYWORDS = [
    r"\bминфин\w*\b",
    r"\bбюджет\w*\b",
    r"\bндпи\b",
    r"\bроссийск\w+\b",
    r"\bроссии\b",
    r"\bрф\b",
    r"\bсбер\w*\b",
    r"\bнефтедоход\w*\b",
    r"\bнефтегаздоход\w*\b",
    r"\bминэк\w*\b",
    r"\bналог[оа]вая?\s+(?:цена|формула)\b",
]

# Rule #1 — asset-specific keywords
_WTI_KEYWORDS = [
    r"\bwti\b",
    r"\bамериканск\w+\s+нефт",
    r"\bus\s+нефт",
    r"\bтехасск\w+",
]
_BRENT_ONLY_KEYWORDS = [
    r"\bbrent\b",
    r"\bбрент\w*\b",
    r"\bсевероморск\w+",
]
_URALS_KEYWORDS = [
    r"\burals\b",
    r"\bурал\w*\s+нефт",
]
_TTF_KEYWORDS = [
    r"\bttf\b",
    r"\bевропейск\w+\s+газ",
    r"\bевропейский\s+газовый",
    r"\bниде\w+\s+газ",
]
_HENRY_HUB_KEYWORDS = [
    r"\bhenry\s*hub\b",
    r"\bамериканск\w+\s+газ",
    r"\bus\s+газ",
    r"\bхенри[\s\-]?хаб",
]

# Generic asset triggers.
# `газ\w{0,3}` ловит «газ/газа/газом/газами» и НЕ цепляет «Газпром».
_OIL_KEYWORDS = [r"\bнефт\w*\b", r"\boil\b", r"\bcrude\b"]
_GAS_KEYWORDS = [r"\bгаз\w{0,3}\b", r"\bgas\b", r"\bnatural\s+gas\b"]


# =============================================================================
# Horizon extraction (rule #3)
# =============================================================================

# 1d / 1w / завтра / неделя — out of forecast scope (дей-трейдинг).
_DAILY_TRIGGER = re.compile(
    r"\b(?:на\s+)?(?:завтра|tomorrow|сутки|на\s+день|на\s+неделю|"
    r"за\s+(?:день|неделю)|\d+\s*(?:d|нед[еа])\b)",
    re.IGNORECASE,
)

_HORIZON_NUMERIC = re.compile(
    r"\b(\d+)\s*(m|месяц(?:ев|а)?|мес|y|год(?:а|ов)?|лет)\b",
    re.IGNORECASE,
)
_HORIZON_MONTH_NAMED = re.compile(r"\bна\s+месяц\b", re.IGNORECASE)
_HORIZON_QUARTER = re.compile(r"\bна\s+квартал\b", re.IGNORECASE)
_HORIZON_HALF = re.compile(r"\bна\s+полгод[ау]?\b", re.IGNORECASE)
_HORIZON_YEAR_NAMED = re.compile(r"\bна\s+(?:год|годовой|год\w*)\b", re.IGNORECASE)


# =============================================================================
# Helpers
# =============================================================================


def _matches_any(query: str, patterns: list[str]) -> bool:
    return any(re.search(p, query, re.IGNORECASE) for p in patterns)


# =============================================================================
# Public API
# =============================================================================


def extract_horizon(query: str) -> tuple[Optional[Horizon], Optional[str]]:
    """Извлечь горизонт из запроса.

    Returns:
        (horizon, refuse_reason).
        - horizon != None, reason == None: горизонт извлечён.
        - horizon == None, reason != None: refusal (rule #3 — 1d/1w или >=18m).
        - horizon == None, reason == None: явного горизонта в запросе нет —
          caller выбирает default (обычно 3m).
    """
    if _DAILY_TRIGGER.search(query):
        return None, (
            "Дей-трейдинг (1d/1w/завтра) — не наша область. "
            "На коротких сроках Random Walk + futures curve доминируют, "
            "стат-модели не дают добавленной ценности. Используй >=1m."
        )

    if _HORIZON_MONTH_NAMED.search(query):
        return Horizon.M1, None
    if _HORIZON_QUARTER.search(query):
        return Horizon.M3, None
    if _HORIZON_HALF.search(query):
        return Horizon.M6, None
    if _HORIZON_YEAR_NAMED.search(query):
        return Horizon.M12, None

    m = _HORIZON_NUMERIC.search(query)
    if m is None:
        return None, None

    n = int(m.group(1))
    unit_text = m.group(2).lower()
    if unit_text.startswith(("y", "год", "лет")):
        n_months = n * 12
    else:
        n_months = n

    if n_months >= 18:
        return None, (
            f"Точечный прогноз на {n_months} месяцев бесполезен — "
            "стат-модели проигрывают сценарным подходам на горизонтах >=18m. "
            "Перенаправь на сценарии в RAG-корпусе: WOO 2025 (до 2050), "
            "IEA Oil 2025 (до 2030), ИНЭИ Прогноз, Энергостратегия РФ-2050."
        )

    if n_months in {1, 3, 6, 12}:
        return Horizon(f"{n_months}m"), None

    # Round to nearest supported (1, 3, 6, 12).
    nearest = min((1, 3, 6, 12), key=lambda v: abs(v - n_months))
    return Horizon(f"{nearest}m"), None


def classify_intent(query: str) -> Intent:
    """Rule-based классификация запроса в Intent.

    См. модуль-docstring для алгоритма (earliest-match-wins).
    """
    q = query.strip()
    if not q:
        return Intent(
            type=IntentType.OUT_OF_SCOPE,
            refuse_reason="Пустой запрос — нечего классифицировать.",
            matched_rule="empty_query",
        )

    # Rule #5: Russian gas direct → refusal
    if _matches_any(q, _RUSSIAN_GAS_PATTERNS):
        return Intent(
            type=IntentType.RUSSIAN_GAS_REFUSAL,
            refuse_reason=(
                "Прямых daily-котировок внутреннего российского газа в открытых "
                "источниках нет (СПбМТСБ за коммерческим каналом, CBR-feed "
                "мёртв с 2022). Индикаторы: TTF (EUR, EU-направление) — proxy "
                "экспорта; GAZP акции — рыночная оценка эмитента. Для конкретных "
                "цифр — RAG: Газпром AR/РСБУ, Минэнерго, Энергостратегия РФ-2050."
            ),
            matched_rule="rule_5_russian_gas",
        )

    # Rule #3: horizon refuse-trigger
    horizon, h_reason = extract_horizon(q)
    if h_reason is not None:
        return Intent(
            type=IntentType.OUT_OF_SCOPE,
            refuse_reason=h_reason,
            matched_rule="rule_3_horizon",
        )

    has_oil = _matches_any(q, _OIL_KEYWORDS)
    has_gas = _matches_any(q, _GAS_KEYWORDS)
    has_ru_context = _matches_any(q, _RU_CONTEXT_KEYWORDS)

    # Rule #1: WTI explicit
    if _matches_any(q, _WTI_KEYWORDS):
        return Intent(
            type=IntentType.FORECAST_SIMPLE,
            forecast_assets=["wti"],
            forecast_horizon=horizon,
            matched_rule="rule_1_wti",
        )

    # Rule #1: Brent explicit (без РФ-контекста — иначе уйдёт в branch ниже)
    if _matches_any(q, _BRENT_ONLY_KEYWORDS) and not has_ru_context:
        return Intent(
            type=IntentType.FORECAST_SIMPLE,
            forecast_assets=["brent"],
            forecast_horizon=horizon,
            matched_rule="rule_1_brent_explicit",
        )

    # Rule #1: Urals explicit OR oil + РФ-контекст → 3 актива параллельно
    if _matches_any(q, _URALS_KEYWORDS) or (has_oil and has_ru_context):
        return Intent(
            type=IntentType.FORECAST_WITH_CONTEXT,
            forecast_assets=["brent", "urals", "urals_minfin_blend"],
            forecast_horizon=horizon,
            matched_rule="rule_1_oil_ru_context",
        )

    # Rule #1: TTF explicit
    if _matches_any(q, _TTF_KEYWORDS):
        return Intent(
            type=IntentType.FORECAST_SIMPLE,
            forecast_assets=["ttf"],
            forecast_horizon=horizon,
            matched_rule="rule_1_ttf",
        )

    # Rule #1: Henry Hub explicit
    if _matches_any(q, _HENRY_HUB_KEYWORDS):
        return Intent(
            type=IntentType.FORECAST_SIMPLE,
            forecast_assets=["henry_hub"],
            forecast_horizon=horizon,
            matched_rule="rule_1_henry_hub",
        )

    # Rule #1: generic gas → US + EU benchmarks (топ-3 не натягивается, см. ADR-0013)
    if has_gas:
        return Intent(
            type=IntentType.FORECAST_SIMPLE,
            forecast_assets=["henry_hub", "ttf"],
            forecast_horizon=horizon,
            matched_rule="rule_1_gas_default",
        )

    # Rule #1: generic oil → brent (мировой benchmark, см. ADR-0013 §1)
    if has_oil:
        return Intent(
            type=IntentType.FORECAST_SIMPLE,
            forecast_assets=["brent"],
            forecast_horizon=horizon,
            matched_rule="rule_1_oil_default",
        )

    # Не покрыто доменными правилами
    return Intent(
        type=IntentType.OUT_OF_SCOPE,
        refuse_reason=(
            "Запрос не покрыт доменными правилами forecast-аналитика. "
            "Поддерживаются прогнозы цен нефти/газа (brent, wti, urals, "
            "urals_minfin_blend, henry_hub, ttf, moexog, gazp, nvtk) на "
            "горизонтах 1m / 3m / 6m / 12m. Для запросов про сценарии, "
            "новости и фундаменталы — дождись интеграции RAG / web-search."
        ),
        matched_rule="no_keyword_match",
    )


__all__ = ["classify_intent", "extract_horizon"]
