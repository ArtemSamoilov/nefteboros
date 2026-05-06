---
name: neftegaz_analyst
description: Старший аналитик нефтегазового рынка — единый entry-point в analyst LangGraph subgraph (классификация intent + forecast + synthesis).
version: 0.1.0
type: extension
entry: plugin.py
permissions: [tool, route]
env_from_settings: []
when_to_use: User asks about oil/gas markets, prices, OPEC+, sanctions, supply/demand, Brent/WTI/Urals/TTF/Henry Hub, forecasts, Russian budget oil prices, Минфин budget formula.
---

# Skill: neftegaz_analyst

Skill — тонкая обёртка вокруг `nefteboros.graphs.analyst_graph` (см. [ADR-0014](../../docs/adr/0014-langgraph-subgraph.md), [ADR-0015](../../docs/adr/0015-llm-disambiguate.md), [ADR-0016](../../docs/adr/0016-forecast-skill.md)). Граф сам делает classify_intent (rule-based + GigaChat-2-Max LLM fallback), вызов `forecast()`, synthesis с дисклеймерами, валидацию цитат. Skill только expose'ит этот pipeline через PluginAPI v1.

> Документ — review-pack для трёх AI-ревьюеров `review_skill` pipeline. Реальная инструкция агенту лежит в `tool.description` (которую видит LLM при tool selection). Body здесь — для humans.

## Зарегистрированные surfaces

| Surface | Имя | Назначение |
|---|---|---|
| Tool   | `analyst_query` (runtime: `ext_<len>_<token>_analyst_query`) | Единый entry-point. На вход — вопрос пользователя. На выход — JSON с synthesis, intent, citations, validation_warnings, forecast_errors. |
| Route  | `GET /api/extensions/neftegaz_analyst/health` | healthcheck. Проверяет, что skill загружен. |

UI tab отсутствует — этот skill не предоставляет визуальных компонентов; используется через chat и `/api/state` каталог. Widgets и поверхности UI добавятся в `feature/analyst-ui-widget` (не этот PR).

## Permissions

- `tool` — `register_tool` (analyst_query).
- `route` — `register_route` (healthcheck).

Без `widget` (нет UI tab), без `read_settings` (env'ы — `OUROBOROS_MODEL`, `GIGACHAT_*`, `OPENAI_COMPATIBLE_*` — читаются `os.environ` напрямую под graph layer'ом, не PluginAPI).

## Архитектура

```
Ouroboros loop
    │
    │ tool_call("ext_<len>_<token>_analyst_query", {query: ...})
    ▼
plugin.py::_tool_analyst_query(query)
    │
    │ lazy import nefteboros.graphs.analyst_graph
    │
    ▼
build_analyst_graph().ainvoke(GraphState(query))
    │
    │ classify_intent (rule-based)
    │   ↓ no_keyword_match → llm_disambiguate (GigaChat-2-Max)
    │ forecast_call (lazy import forecast() — pandas/statsmodels stack)
    │ synthesize (ouroboros.llm router → kimi-k2 / gigachat / etc)
    │ validate_citations
    │
    ▼
JSON {synthesis, intent, citations, validation_warnings, forecast_errors}
```

Lazy import графа критически важен: при cold-start Ouroboros (особенно в CI без domain deps) skill manifest парсится без триггера тяжёлых импортов; pandas/numpy/statsmodels/yfinance/sklearn/langgraph/langchain-gigachat подгружаются только при реальном tool-call.

## Безопасность

- **Никаких arbitrary code paths** — handler принимает `query: str`, длина ограничена ≤2000 символов, server-side normalize. Нет shell-вызовов, нет file I/O за пределы forecast cache.
- **Network**: forecast() через `yfinance` / `EIA API` / `MOEX ISS` (см. ADR-0012). LLM-вызовы — через `ouroboros.llm` (для synthesize) и `langchain-gigachat` (для llm_disambiguate). Все фиксированные хосты, не контролируемые пользовательским вводом.
- **Permissions** строго минимальные: `[tool, route]`. Нет `subprocess`, `fs`, `widget`, `read_settings`, `iframe_raw`.

## Тестирование

- Unit-тесты узлов графа: 68/68 passed (см. ADR-0014/0015 + `tests/test_intent_classifier.py`, `test_graph_smoke.py`, `test_llm_disambiguate.py`).
- Smoke-тесты skill'а: `tests/test_neftegaz_skill_smoke.py` — discover_skills находит skill, manifest валиден без warnings, register(api) выполняется через capture-mock, tool возвращает корректный JSON-shape.
- Golden-eval intent classifier'а: 100 запросов, type_accuracy 0.98 hybrid (см. `docs/experiments/intent_classifier.md`).

## Известные ограничения

- **Synthesize без RAG/web overlay** до merge `feature/rag-integration` / `feature/web-integration` — ответы тонкие, упоминают «RAG/web pending».
- **Системный промпт Ouroboros** ещё не настроен под роль «аналитик». До `feature/system-prompt-analyst` агент в default режиме («I am self-modifying AI») может не выбрать `analyst_query` на нефтегазовые запросы. После manual `/skill enable neftegaz_analyst` + soft hint в первом message — работает.
- **Latency**: первый вызов после cold-start — 5-15 секунд (lazy import + LLM round-trip). Последующие — 2-5 сек.
- **Cost**: ~50-80 руб за 100 LLM-вызовов в hybrid режиме (см. `docs/experiments/intent_classifier.md`).

## Связанные документы

- [ADR-0001](../../docs/adr/0001-fork-ouroboros.md) §«Логика приоритизации источников» — место graph внутри analyst_query tool'а.
- [ADR-0014](../../docs/adr/0014-langgraph-subgraph.md) — minimal-graph baseline (classify+forecast+synthesize+validate).
- [ADR-0015](../../docs/adr/0015-llm-disambiguate.md) — hybrid LLM disambiguate через GigaChat-2-Max.
- [ADR-0016](../../docs/adr/0016-forecast-skill.md) — этот skill (тонкий wrapper).
- [docs/architecture.md](../../docs/architecture.md) — high-level схема.
