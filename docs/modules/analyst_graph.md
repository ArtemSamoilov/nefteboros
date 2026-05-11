# Модуль: Analyst Graph (главный пайплайн)

LangGraph subgraph, склеивающий маршрутизацию интента, прогноз и синтез ответа. Это «корень» агентного потока: всё, что приходит из Ouroboros через инструмент `analyst_query`, идёт сюда.

## Точка входа

- `nefteboros/graphs/analyst_graph.py:102` — `build_analyst_graph() -> CompiledStateGraph`.
- `nefteboros/graphs/analyst_graph.py:159` — `invoke_with_trace(graph, state)` — обёртка для CLI и eval с открытым Langfuse-трейсом (в prod через Ouroboros используется `traced_tool`).
- Внешний вызывающий: `skills/neftegaz_analyst/plugin.py:233` (`@traced_tool(name="analyst_query")`).

## Входы / выходы

**Вход:** `GraphState(query: str, ...)` — `nefteboros/graphs/state.py`.

**Выход:** словарь с полями (`intent`, `forecast`, `synthesis`, `citation_report`, …). Финальный текст ответа — `result["synthesis"]`.

## Топология

```
classify_intent (по правилам)
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
- [ADR-0015](../adr/0015-llm-disambiguate.md) — гибрид правил и LLM-разрешения неоднозначности для `no_keyword_match`.
- [ADR-0024 (observability)](../adr/0024-observability-langfuse.md) — оборачивание узлов через `@observe` в builder'е (а не на доменных узлах).
- В коде есть ссылка на «ADR-0025 (observability — `@observe` через wrap при `add_node`)» (`analyst_graph.py:4`) — **файла `0025-*.md` в репо нет**. Похоже, в комментарии перепутан номер с `0024-observability-langfuse.md`. Сигнал координатору.

## Метрики

**Инструментация есть.** Каждый узел оборачивается `observe(name=...)` при `add_node` (`analyst_graph.py:119-132`):

| Узел | Имя span'а | as_type | Что зафиксировано в Langfuse |
|---|---|---|---|
| `_classify_node` | `classify_intent` | span | latency, status (без LLM) |
| `llm_disambiguate` | `llm_disambiguate` | generation | latency, status; `log_llm_usage` вызывается, но cost/tokens/model — `null` (backlog v2.4) |
| `forecast_call` | `forecast_call` | span | latency, status (OU детерминирован) |
| `synthesize` | `synthesize` | generation | latency, status; `log_llm_usage` вызывается, но cost/tokens/model — `null` (backlog v2.4) |
| `validate_citations` | `validate_citations` | span | latency, status |

Корневой span `user_request` открывается в `nefteboros/observability/_ouroboros_patches.py:299`, дочерние span'ы (`ouroboros_chat`, `analyst_query`, узлы) пристёгиваются через OTel-контекст.

> ℹ **Enrichment'а cost / tokens / model name в generation-spans Langfuse сейчас нет** (verified дампом 10 generations за окно 15 мин). `log_llm_usage` вызывается в `synthesize._call_llm` и `llm_disambiguate._call_llm`, но финальные spans приходят с `provided_model_name=null`, `usage_details=null`, `total_cost=null`. Полная реализация — backlog v2.4.

JSON-трейс параллельно в `metrics/runs/<ts>/trace.jsonl` (см. [ADR-0024 observability](../adr/0024-observability-langfuse.md)).

## Известные ограничения

- Узлы `forecast_call` и `validate_citations` обёрнуты в графе, но внутренние шаги (поиск калибровки, регекс-парсинг) не разбиваются на под-span'ы.
