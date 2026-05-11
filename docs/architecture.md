# Архитектура nefteboros

> Документ описывает целевую архитектуру системы. По мере реализации актуализируется.
>
> Оригинальные документы Ouroboros (upstream) — в [docs/upstream/](upstream/).

## High-level

```mermaid
flowchart TB
    User([Пользователь])
    User -->|Web UI| Web[Streamlit / Ouroboros web]

    Web --> Core

    subgraph Core["nefteboros core (форк Ouroboros)"]
        OurLoop["Ouroboros loop — диспетчер инструментов"]
        AnalystTool["Tool: analyst_query"]
        ForecastTool["Tool: brent_forecast"]
        OurLoop --> AnalystTool
        OurLoop --> ForecastTool
    end

    subgraph LangGraph["LangGraph subgraph (внутри analyst_query)"]
        Classify[classify_intent]
        Router{route}
        RAG[retrieve_rag]
        Web2[web_search]
        Forecast[forecast_call]
        Synth[synthesize_with_citations]
        Validate[validate_citations]

        Classify --> Router
        Router -->|reports| RAG
        Router -->|news/current| Web2
        Router -->|prediction| Forecast
        RAG --> Synth
        Web2 --> Synth
        Forecast --> Synth
        Synth --> Validate
    end

    AnalystTool --> Classify
    ForecastTool --> Forecast

    RAG -->|hit@k=5| ChromaDB[("ChromaDB — BGE-M3 embeddings")]
    Web2 --> Brave["Brave Search API + tier-1/2 фильтр"]
    Forecast --> OU["OU regime — ограниченный CI 80/95%"]

    Synth -.->|LLM call| LLMRouter
    Classify -.->|LLM call| LLMRouter

    subgraph LLMRouter["LLM router"]
        GigaChat["GigaChat-2-Max — llm_disambiguate"]
        Hydra["Hydra — kimi-k2p6 (primary), glm/deepseek (опционально)"]
        AItunnel["AItunnel — резервный провайдер (OpenAI-compatible)"]
    end
```

## Поток обработки запроса

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant UI as Web UI
    participant L as Ouroboros loop
    participant T as analyst_query tool
    participant G as LangGraph subgraph
    participant R as RAG (Chroma)
    participant W as Brave Search
    participant F as Forecast module
    participant S as Synthesizer + LLM

    U->>UI: «Прогноз Brent на 3 месяца с учётом сокращения ОПЕК+»
    UI->>L: chat message
    L->>T: tool_call(analyst_query, query=...)
    T->>G: invoke(query)
    G->>G: classify_intent → "forecast + context"
    G->>R: retrieve(query, k=5)
    R-->>G: chunks с metadata [report_name, date, page]
    G->>F: forecast_brent(horizon=3mo)
    F-->>G: prediction + CI 80/95% + interpretation
    G->>W: search("OPEC+ production cut latest")
    W-->>G: tier-1 articles
    G->>S: synthesize(rag_chunks, forecast, web_articles)
    S-->>G: ответ с маркировкой источников
    G->>G: validate_citations (regex match с извлечёнными чанками)
    G-->>T: validated answer
    T-->>L: tool_result
    L-->>UI: assistant message
    UI-->>U: ответ + источники
```

## Компонентная схема

| Слой | Компонент | Файл / директория | Статус |
|---|---|---|---|
| **Ядро (форк)** | tool loop + agent | `ouroboros/loop.py`, `ouroboros/agent.py` | Оставляем |
| | LLM роутер | `ouroboros/llm.py` | Реализован (ADR-0007) |
| | Tools registry | `ouroboros/tools/` | Расширим |
| | Skill loader | `ouroboros/skill_loader.py` | Используем |
| | Safety / sandbox | `ouroboros/safety.py` | Не трогаем |
| | Web UI | `web/` | Косметический ребрендинг |
| **Выпиливаем** | Self-modify | `consciousness.py`, `reflection.py`, `deep_self_review.py`, `improvement_backlog.py` | Удалить |
| | Marketplace | `marketplace*.py` | Удалить |
| | A2A protocol | `a2a_*.py` | Удалить |
| **Доменное (новое)** | RAG | `nefteboros/rag/` | Реализован (ADR-0011/0016) |
| | Forecast | `nefteboros/forecast/` | Реализован (ADR-0012/0013/0024-ou-regime) |
| | Web search | `nefteboros/search/` | Реализован (ADR-0022) — Brave + tier-фильтр + определение языка |
| | LLM-адаптеры | `nefteboros/llm/` | Реализован (ADR-0007/0008) |
| | LangGraph | `nefteboros/graphs/` | Реализован (ADR-0014/0015) |
| | Валидатор цитат | `nefteboros/citations/` | RAG — реализован, web — отдельный PR |
| | Промпты | `nefteboros/prompts/` | Реализован (ADR-0019) |
| **Skill** | neftegaz_analyst | `skills/neftegaz_analyst/` | Реализован |
| **Eval** | Скрипты | `scripts/eval/` | Реализован (e2e, rag, forecast, routing); citations — заглушка (план v2.4) |
| | Датасеты | `datasets/` | Реализован |
| | Метрики | `metrics/runs/` | Реализован |
| **Деплой** | Docker | `deploy/` | Реализован (multi-arch GHCR) |

## Принципы

1. **Каждый подграф измеряем.** RAG — hit@k/MRR; routing — accuracy/F1; citations — precision/recall (план v2.4); forecast — MAPE/RMSE/coverage; e2e — golden dialogues.
2. **Цитирование — критичное место.** Ответ агента не должен содержать «источников» без подтверждения. Пост-валидатор `nefteboros/citations/` проверяет, что каждая ссылка вида `[Отчёт OPEC MOMR, март 2025]` соответствует чанку, реально извлечённому RAG'ом.
3. **Кросс-язычность.** Эмбеддинги мультиязычные (BGE-M3) — отчёты OPEC/IEA/EIA на английском, пользователь спрашивает по-русски.
4. **Маршрутизация — явная.** Не «пусть LLM решит», а классификатор намерения с явным набором веток. Это даёт измеримость.
5. **Safe defaults.** Tools зарегистрированы в Ouroboros' safety policy с правильным labelling.

## Логика приоритизации источников (ТЗ §2.4)

Реализуется в `nefteboros/graphs/analyst_graph.py`:

```
classify_intent →
    ├─ "report_topic" (отрасль, OPEC решения, fundamentals) → RAG → если confidence > 0.7, синтез на RAG
    ├─ "current_data" (цены, последние новости) → web → синтез на web
    ├─ "forecast" → forecast tool + RAG для контекста + web для актуальности → синтез
    └─ "out_of_scope" → отказной ответ с предложением переформулировать
```

Если RAG-confidence < 0.7 для report_topic → fallback на web.

## Логика фильтрации источников (ТЗ §2.3)

`nefteboros/search/tiers.py` (см. ADR-0022) содержит:
- **TIER1** (приоритет): Reuters, Bloomberg, FT, WSJ, Argus Media, S&P Platts, Wood Mackenzie, Energy Intel, Rystad, IEA, OPEC, EIA, РБК, Ведомости, Коммерсант, Интерфакс, ТАСС, OilPrice.
- **TIER2** (допустимы): CNBC, Forbes, RG.ru, Expert, Neftegaz.ru, Energyland, Lenta.
- **BLACKLIST** (отбрасываются всегда): Reddit, Quora, соцсети, Dzen/Yandex.zen, LiveJournal, Pikabu, Medium.

Реализация: post-filter по hostname после Brave API. Полная замена через ENV — `NEFTEBOROS_WEB_TIER1_HOSTS=...`, `NEFTEBOROS_WEB_TIER2_HOSTS=...`, `NEFTEBOROS_WEB_BLACKLIST_HOSTS=...`.

Определение языка (`nefteboros/search/lang.py`): доля кириллицы ≥ 30% → RU (Brave `search_lang=ru, country=RU`); иначе EN. RU-запрос ловит RU-tier1, EN — EN-tier1.

## Observability (ADR-0024-observability-langfuse)

Серверные трейсы всех узлов LangGraph + cost/latency идут в **Langfuse Cloud**. Декораторы `observe(name=...)` навешиваются на узлы в `nefteboros/graphs/analyst_graph.py:build_analyst_graph` через wrap при `add_node` — файлы `nodes/*.py` остаются без декораторов. Entry-points (CLI, ouroboros tool, eval скрипты) используют `invoke_with_trace(graph, state)` для открытия top-level trace на запрос. LLM-вызовы внутри узлов прикрепляют tokens/cost к текущему span'у через `log_llm_usage(usage)` (модуль `nefteboros/observability/`). Параллельно с Langfuse пишется JSON-trace в `metrics/runs/<ts>/trace.jsonl` — backup для demo-режима без Langfuse-аккаунта (`LANGFUSE_ENABLED=false`). Cost считается через иерархию: наши `COST_RATES` (kimi-k2p6, GigaChat-2-Max) → fallback `ouroboros.pricing.estimate_cost` → null.

## Эволюция

Документ обновляется на каждом значимом PR'е. История изменений — в `docs/changelog/`.
