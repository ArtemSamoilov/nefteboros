# Модуль: Routing (intent classification + LLM disambiguate)

Маршрутизация запроса по типу: forecast (с указанием актива/горизонта), russian_gas_refusal, out_of_scope, no_keyword_match → LLM. Двухуровневый: сначала rule-based по словарю, потом LLM-fallback на неоднозначных запросах.

## Точка входа

- `nefteboros/graphs/intents.py` — `classify_intent(query: str) -> IntentClassification` (rule-based).
- `nefteboros/graphs/nodes/llm_disambiguate.py` — async LLM-узел для `no_keyword_match` (вызывает GigaChat или primary LLM, см. ADR-0015).
- В графе: `analyst_graph.py:120` (rule-based) и `analyst_graph.py:122-125` (LLM disambiguate).

## Входы / выходы

**Вход:** `state.query: str`.

**Выход:** `state.intent: IntentClassification` с полями:
- `type`: `FORECAST_SIMPLE` / `FORECAST_WITH_CONTEXT` / `RUSSIAN_GAS_REFUSAL` / `OUT_OF_SCOPE` / `NO_KEYWORD_MATCH` (см. `state.py:IntentType`).
- `matched_rule`: какое правило сработало (rule_1..rule_5 или `no_keyword_match`).
- `assets`: распознанные активы (`brent`, `wti`, `urals`, …).
- `horizon`: распознанный горизонт (если applicable).

После классификации conditional edge `_route_after_classify_initial` (`analyst_graph.py:60`) направляет в `forecast_call` / `synthesize` / `llm_disambiguate`.

## Ключевые ADR

- [ADR-0014](../adr/0014-langgraph-subgraph.md) — структура intent типов.
- [ADR-0015](../adr/0015-llm-disambiguate.md) — гибрид rule-based + LLM, почему LLM только на `no_keyword_match`.

## Метрики

**Есть инструментация:**

- `classify_intent` — span (`analyst_graph.py:119-121`). Rule-based, без LLM cost.
- `llm_disambiguate` — generation span (`analyst_graph.py:122-125`). Cost/tokens пишутся через `log_llm_usage` в `nefteboros/graphs/nodes/llm_disambiguate.py:184`.

**Eval скрипт:** `scripts/eval/eval_intent_classifier.py`:
- Метрики: `type_accuracy`, `assets_jaccard_mean`, `horizon_match_rate`, precision/recall/F1.
- Modes: `--no-llm` (только правила), `--llm` (гибрид с GigaChat).
- Output: `metrics/runs/<date>_intent_<rules|llm>_<sha>.json`.

**Eval `scripts/eval/eval_routing.py`** — отдельный mini-скрипт. Что именно измеряет — нужно перепроверить (поверхностно отличий от `eval_intent_classifier.py` не нашли при разведке). Сигнал для координатора: возможно дублирующая инфраструктура.

## Известные ограничения

- Запросы про российский газ всегда уходят в refusal (`RUSSIAN_GAS_REFUSAL`) — это product decision, не bug. Источник: ТЗ требует нефтяной фокус, газ упомянут только проксями (Gazprom equity, TTF/HH в forecast).
- `eval_routing.py` vs `eval_intent_classifier.py` — нет явного назначения второго; возможно legacy.
