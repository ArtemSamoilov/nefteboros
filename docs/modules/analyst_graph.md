# Модуль: Analyst Graph (главный пайплайн)

LangGraph subgraph, склеивающий маршрутизацию интента, прогноз и синтез ответа. Это «корень» агентного flow: всё, что приходит из Ouroboros tool dispatch через `analyst_query`, идёт сюда.

## Точка входа

- `nefteboros/graphs/analyst_graph.py:102` — `build_analyst_graph() -> CompiledStateGraph`
- `nefteboros/graphs/analyst_graph.py:159` — `invoke_with_trace(graph, state)` — обёртка для CLI / eval с открытым Langfuse trace (production через Ouroboros использует `traced_tool`).
- Внешний caller: `skills/neftegaz_analyst/plugin.py:233` (`@traced_tool(name="analyst_query")`).

## Входы / выходы

**Вход:** `GraphState(query: str, ...)` — `nefteboros/graphs/state.py`.

**Выход:** dict с полями (`intent`, `forecast`, `synthesis`, `citation_report`, …). Финальный текст ответа — `result["synthesis"]`.

## Топология

```
classify_intent (rule-based)
    │
    ├─ refusal (rule_5 / rule_3)             → synthesize
    ├─ forecast_simple / forecast_with_ctx   → forecast_call → synthesize
    └─ no_keyword_match                      → llm_disambiguate
                                                 ├─ refusal   → synthesize
                                                 └─ forecast  → forecast_call → synthesize
synthesize → validate_citations → END
```

Узлы и их роли описаны в [routing.md](routing.md), [forecast.md](forecast.md), [citation.md](citation.md).

## Ключевые ADR

- [ADR-0014](../adr/0014-langgraph-subgraph.md) — минимальный baseline графа.
- [ADR-0015](../adr/0015-llm-disambiguate.md) — гибрид rule-based + LLM disambiguate для `no_keyword_match`.
- [ADR-0024 (observability)](../adr/0024-observability-langfuse.md) — wrap узлов через `@observe` в builder'е (а не на доменных узлах).
- В коде есть ссылка на «ADR-0025 (observability — `@observe` через wrap при `add_node`)» (`analyst_graph.py:4`) — **файл `0025-*.md` отсутствует в репо**. Похоже, в комментарии перепутан номер с `0024-observability-langfuse.md`. Сигнал для координатора.

## Метрики

**Есть инструментация.** Каждый узел оборачивается `observe(name=...)` при `add_node` (`analyst_graph.py:119-132`):

| Узел | Span name | as_type | Где cost/tokens |
|---|---|---|---|
| `_classify_node` | `classify_intent` | span | n/a (rule-based) |
| `llm_disambiguate` | `llm_disambiguate` | generation | `log_llm_usage` в `nodes/llm_disambiguate.py:184` |
| `forecast_call` | `forecast_call` | span | n/a (OU детерминирован) |
| `synthesize` | `synthesize` | generation | `log_llm_usage` в `nodes/synthesize.py:188` |
| `validate_citations` | `validate_citations` | span | n/a (regex + lookup) |

Корневой `user_request` span открывается в `nefteboros/observability/_ouroboros_patches.py:299`, дочерние spans (`ouroboros_chat`, `analyst_query`, узлы) пристёгиваются через OTel context propagation.

JSON-trace параллельно в `metrics/runs/<ts>/trace.jsonl` (см. [ADR-0024 observability](../adr/0024-observability-langfuse.md)).

## Известные ограничения

- Root trace loss на коротких диалогах ~33% (async batch flush race) — см. changelog [2026-05-11-observability-post-span-flush.md](../changelog/2026-05-11-observability-post-span-flush.md). Mitigation backlog v2.4.
- Узлы `forecast_call` и `validate_citations` — обёрнуты в графе, но внутренние шаги (calibration lookup, regex-парсинг) не разбиваются на sub-spans.
