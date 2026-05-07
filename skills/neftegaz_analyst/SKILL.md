---
name: neftegaz_analyst
description: Старший аналитик нефтегазового рынка — два независимых tools (analyst_query для forecast/synthesis + rag_search для documentary поиска по 802-чанковому корпусу).
version: 0.2.0
type: extension
entry: plugin.py
permissions: [tool, route]
env_from_settings: []
when_to_use: User asks about oil/gas markets, prices, OPEC+, sanctions, supply/demand, Brent/WTI/Urals/TTF/Henry Hub, forecasts, Russian budget oil prices, факты из отчётов OPEC/IEA/EIA, корпоративные отчёты Газпрома/Роснефти/Лукойла, Энергостратегия РФ, geopolitical analysis.
---

# Skill: neftegaz_analyst

Skill экспортирует **два независимых tools** через PluginAPI v1:
1. **`analyst_query`** — analyst LangGraph subgraph (classify_intent + forecast + synthesis). См. [ADR-0014](../../docs/adr/0014-langgraph-subgraph.md), [ADR-0015](../../docs/adr/0015-llm-disambiguate.md), [ADR-0016](../../docs/adr/0016-forecast-skill.md).
2. **`rag_search`** — прямой retrieval из RAG-корпуса (802 chunks, см. [docs/experiments/rag-full-eval-report.md](../../docs/experiments/rag-full-eval-report.md)). Тонкая обёртка над `nefteboros.rag.retriever.Retriever`. См. [ADR-0018](../../docs/adr/0018-rag-search-tool.md).

**Multi-tool архитектура** обоснована в ADR-0018: ТЗ §2.4 требует приоритизации RAG → web → forecast как **agent decision** (системный промпт), не fixed graph routing.

> Документ — review-pack для трёх AI-ревьюеров `review_skill` pipeline. Реальная инструкция агенту лежит в `tool.description` каждого tool'а (которую видит LLM при tool selection). Body здесь — для humans.

## Зарегистрированные surfaces

| Surface | Имя | Назначение |
|---|---|---|
| Tool   | `analyst_query` | Единый entry-point в analyst graph. На вход — вопрос. На выход — JSON с synthesis, intent, citations, validation_warnings, forecast_errors. |
| Tool   | `rag_search` | Прямой top-k retrieval из RAG-корпуса. На вход — query + опц. k. На выход — JSON со списком chunks (text, source_title, section_path, page_start/end, score). |
| Route  | `GET /api/extensions/neftegaz_analyst/health` | healthcheck. Проверяет, что skill загружен. |

UI tab отсутствует — этот skill не предоставляет визуальных компонентов; используется через chat и `/api/state` каталог. Widgets и поверхности UI добавятся в `feature/analyst-ui-widget` (не этот PR).

## Permissions

- `tool` — `register_tool` × 2 (analyst_query + rag_search).
- `route` — `register_route` (healthcheck).

Без `widget` (нет UI tab), без `read_settings` (env'ы — `OUROBOROS_MODEL`, `GIGACHAT_*`, `OPENAI_COMPATIBLE_*`, `NEFTEBOROS_RAG_*` — читаются `os.environ` напрямую под graph/retriever layer'ом, не PluginAPI).

## Когда какой tool вызывать

Решает **агент в Ouroboros loop'е** на основе `tool.description` каждого tool'а. Системный промпт (в отдельном PR `feature/system-prompt-analyst`) прописывает приоритизацию из ТЗ §2.4:

| Запрос | Tool |
|---|---|
| «Что говорит OPEC про квоты?» | **rag_search** — documentary fact из отчёта |
| «Стратегия Новатэка по СПГ?» | **rag_search** — корпоративный отчёт |
| «Прогноз Brent на 3 месяца» | **analyst_query** — расчётный модуль |
| «Минфин нефтегаздоходы 2026» | **analyst_query** (там РФ-budget логика) или **rag_search** (Минэк прогноз СЭР) — агент сам выбирает |
| «Свежие новости рынка» | (будущий) **web_search** в `feature/web-search-integration` |
| «Цена Brent + что повлияет» | **rag_search** + **analyst_query** — агент вызывает оба и синтезирует комбинированный ответ |
| «Криптовалюты» | refusal через `analyst_query` (он умеет out_of_scope) |

## Архитектура

```
Ouroboros loop
    │  агент видит ДВА tools (+ ВЫБИРАЕТ или вызывает оба):
    │
    ├─► tool_call(analyst_query, {query})
    │      ▼
    │   _tool_analyst_query → lazy import analyst_graph
    │      ▼
    │   build_analyst_graph().ainvoke(GraphState(query))
    │      classify_intent → llm_disambiguate? → forecast_call → synthesize → validate
    │      ▼
    │   JSON {synthesis, intent, citations, validation_warnings, forecast_errors}
    │
    └─► tool_call(rag_search, {query, k=5})
           ▼
        _tool_rag_search → lazy import Retriever
           ▼
        Retriever().retrieve(query, k_dense=30, k_final=k)
           BGE-M3 embed → Chroma top-30 → top-k (heading prefix v2 default)
           ▼
        JSON {query, k, total_returned, chunks: [{text, source_title, section_path, page_start, page_end, score, ...}]}
```

**Lazy import критически важен** для обоих tools — при cold-start Ouroboros (особенно в CI без domain deps) skill manifest парсится без триггера тяжёлых импортов:
- `analyst_query`: pandas/numpy/statsmodels/yfinance/sklearn/langgraph/langchain-gigachat
- `rag_search`: chromadb/sentence-transformers/torch (модели BGE-M3 ~2.3 ГБ)

Подгружаются только при реальном tool-call.

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
