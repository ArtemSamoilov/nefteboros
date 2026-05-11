# Модуль: Routing (классификация интента + LLM-разрешение неоднозначности)

Маршрутизация запроса по типу: forecast (с указанием актива и горизонта), russian_gas_refusal, out_of_scope, no_keyword_match → LLM. Двухуровневый процесс: сначала классификация по правилам и словарю, потом LLM-fallback на неоднозначных запросах.

## Точка входа

- `nefteboros/graphs/intents.py` — `classify_intent(query: str) -> IntentClassification` (по правилам).
- `nefteboros/graphs/nodes/llm_disambiguate.py` — асинхронный LLM-узел для `no_keyword_match` (вызывает GigaChat или основную LLM, см. ADR-0015).
- В графе: `analyst_graph.py:120` (по правилам) и `analyst_graph.py:122-125` (LLM disambiguate).

## Входы / выходы

**Вход:** `state.query: str`.

**Выход:** `state.intent: IntentClassification` с полями:
- `type`: `FORECAST_SIMPLE` / `FORECAST_WITH_CONTEXT` / `RUSSIAN_GAS_REFUSAL` / `OUT_OF_SCOPE` / `NO_KEYWORD_MATCH` (см. `state.py:IntentType`).
- `matched_rule`: какое правило сработало (rule_1..rule_5 или `no_keyword_match`).
- `assets`: распознанные активы (`brent`, `wti`, `urals`, …).
- `horizon`: распознанный горизонт (если применим).

После классификации условный переход `_route_after_classify_initial` (`analyst_graph.py:60`) направляет в `forecast_call` / `synthesize` / `llm_disambiguate`.

## Ключевые ADR

- [ADR-0014](../adr/0014-langgraph-subgraph.md) — структура типов интента.
- [ADR-0015](../adr/0015-llm-disambiguate.md) — гибрид правил и LLM, почему LLM только на `no_keyword_match`.

## Метрики

**Инструментация есть:**

- `classify_intent` — span (`analyst_graph.py:119-121`). Срабатывает по правилам, без расхода LLM.
- `llm_disambiguate` — generation span (`analyst_graph.py:122-125`). `log_llm_usage` вызывается в `nefteboros/graphs/nodes/llm_disambiguate.py:184`, но в Langfuse cost / tokens / model name приходят как `null` (latency и статус фиксируются полноценно; enrichment — backlog v2.4).

**Eval-скрипт:** `scripts/eval/eval_intent_classifier.py`:
- Метрики: `type_accuracy`, `assets_jaccard_mean`, `horizon_match_rate`, precision/recall/F1.
- Режимы: `--no-llm` (только правила), `--llm` (гибрид с GigaChat).
- Вывод: `metrics/runs/<date>_intent_<rules|llm>_<sha>.json`.

**Eval `scripts/eval/eval_routing.py`** — отдельный мини-скрипт. Что именно измеряет — нужно перепроверить (поверхностно отличий от `eval_intent_classifier.py` не нашли при разведке). Сигнал координатору: возможно дублирующая инфраструктура.

## Известные ограничения

- Запросы про российский газ всегда уходят в refusal (`RUSSIAN_GAS_REFUSAL`) — это продуктовое решение, не баг. Причина: ТЗ требует нефтяного фокуса, газ упомянут только косвенно (Gazprom equity, TTF/HH в прогнозе).
- `eval_routing.py` против `eval_intent_classifier.py` — нет явного назначения второго; возможно legacy.
