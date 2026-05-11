# Отчёт — AI-агент «Нефтегазовый аналитик»

> ТЗ §4.5. Архитектурный обзор — [docs/architecture.md](architecture.md), история решений — [docs/adr/](adr/).

## 1. Описание

**nefteboros** — агент в роли старшего аналитика нефтегазового рынка. Отвечает на профильные вопросы (Upstream/Midstream/Downstream, Brent/WTI/Urals/ESPO, ОПЕК+, санкции), строит сценарные прогнозы (нефть, газ, MOEX-equity), выполняет гибридный поиск по 25 отраслевым отчётам (RAG) и открытым источникам (web) с маркировкой. Ouroboros — ядро tool-loop, доменная логика — в LangGraph-подграфе. Развёрнут на Timeweb VDS (Docker, Streamlit-web).

## 2. Архитектура

Двухуровневая. **Внешний слой** — форк Ouroboros ([ADR-0001](adr/0001-fork-ouroboros.md)): tool dispatcher, LLM-роутер, safety policy, web UI; из upstream выпилены подсистемы самомодификации, marketplace, A2A. **Внутренний слой** — LangGraph-подграф `analyst_graph` ([ADR-0014](adr/0014-langgraph-subgraph.md)) внутри tool `analyst_query`: детерминированный orchestrator `classify_intent → route → {retrieve_rag | web_search | forecast_call} → synthesize → validate_citations`. Маршрутизация — rule-based regex по 5 правилам disambiguation, не «LLM решит». Полная схема (mermaid + sequence + компонентная таблица) — [docs/architecture.md](architecture.md).

| Слой | Технология |
|---|---|
| Ядро | Ouroboros fork (Python 3.12), Streamlit web |
| Orchestration | LangGraph (analyst_graph) |
| RAG | ChromaDB + BGE-M3 multilingual, 802 чанка |
| Web | Brave Search API + tier-1/2 фильтр + lang-detection |
| Forecast | Regime-conditioned Ornstein-Uhlenbeck per scenario |
| LLM stack | GigaChat-2-Max (primary), Hydra/kimi-k2p6, AItunnel (fallback) |
| Observability | Langfuse Cloud + JSON-trace backup |

## 3. Технологии и обоснование

**LLM stack — GigaChat + Kimi K2 (через Hydra) + AItunnel backup** ([ADR-0007](adr/0007-llm-providers.md)). Разные модели на разные роли, vendor-agnostic OpenAI-compatible транспорт.

- **GigaChat-2-Max** — узел `llm_disambiguate` в `analyst_graph` ([ADR-0015](adr/0015-llm-disambiguate.md), `nefteboros/graphs/nodes/llm_disambiguate.py`): вызывается conditional-edge'ом когда rule-based `classify_intent` возвращает `matched_rule == "no_keyword_match"`. Structured-JSON output, перезаписывает `state.intent`. Модель Sber применена там, где её сильные стороны в русскоязычной семантике дают наибольший выигрыш — на disambiguation неоднозначных пользовательских запросов.
- **Kimi K2 (kimi-k2p6)** — primary worker для синтеза ответа и chunk-tagging. **Open-weights модель Moonshot AI** — при необходимости разворачивается в защищённом контуре Сбера (vLLM / sglang / llama.cpp), без зависимости от внешнего провайдера. В нашем deploy подключается через российский OpenAI-compatible шлюз Hydra; смена на on-prem inference endpoint — это правка `base_url` в env, без изменения кода. 256k context и нативный tool-calling — вторичные плюсы.
- **AItunnel** — backup провайдер (тот же OpenAI-compatible протокол, те же open-weights модели). Подключается автоматически через `OUROBOROS_MODEL_FALLBACK` при empty response от primary.
- **OpenAI / Anthropic** отвергнуты — недоступны из РФ без VPN, неприменимо для prod-агента под Сбер.

**Forecast — Ornstein-Uhlenbeck mean-reverting, не SARIMAX/ARIMA** ([ADR-0024-ou-regime-forecast](adr/0024-ou-regime-forecast.md)). Стат-модели (SARIMAX + GBR ensemble, изначально [ADR-0012](adr/0012-price-tools.md)/[ADR-0013](adr/0013-hybrid-forecasting.md)) дают расходящуюся CI (Var ∝ t): ширина ±30-40% на 12m — неактионабельно. Заменены на OU per scenario: `dS = θ(μ(t) - S)dt + σ dW`, Var → `σ²/(2θ)` bounded. Параметры μ/θ/σ откалиброваны через Kilian elasticity ($12/bbl на 1 mbpd) + bank consensus + regime-specific historical vol. Это даёт структурно-актионабельный CI «при сценарии X target $Y, скорость reversion Z, vol W» — как мыслит senior-аналитик.

**RAG — Chroma + BGE-M3 + крупные чанки** ([ADR-0016](adr/0016-embed-retrieve.md), [ADR-0011](adr/0011-chunking-and-tagging.md)). Multilingual эмбеддинги (1024d) — корпус RU/EN ≈ 41/59. Чанки крупные (target 3000 tok) — Kimi-2.6 работает с длинным контекстом, мелкие чанки дают шум. PDF→Markdown — Marker. Корпус собран от taxonomy вопросов, не «качаем всё» ([docs/corpus.md](corpus.md)).

**LangGraph — детерминированный orchestrator.** Routing forecast/RAG/web — код с unit-тестами, не best-effort LLM-prompt. Системный промпт ([ADR-0019](adr/0019-system-prompt-analyst.md)) задаёт идентичность и форматы цитирования.

## 4. Ограничения

- **Telegram-бот не работает в нашем deploy** — `api.telegram.org` блокируется RKN на уровне сети Timeweb. Mitigation: web UI как primary интерфейс.
- **~33% потерь root-trace в Langfuse на back-to-back eval.** Async batch flush race на коротких диалогах. Trade-off принят: пробовали `force_flush()` — даёт 5/5 root traces, но ломает child propagation (RAG/web/forecast становятся orphan вместо children внутри `user_request`). Иерархия trace важнее покрытия ([changelog post-span-flush](changelog/2026-05-11-observability-post-span-flush.md)). В prod-сценарии (paced WS input) gap значительно меньше.
- **Numeric grounding не реализован.** RAG-валидатор проверяет факт цитирования, но не сверяет числа в ответе с числами в чанках — LLM может процитировать правильный чанк и написать неверное число. Two-tier validator (regex + LLM verifier) — backlog ([Track D4](roadmap-v2.1.md)).
- **Prompt injection guard — defense-in-depth, не silver bullet.** Web-контент оборачивается в `<external_content>` delimiters; system prompt инструктирует игнорировать императивы внутри. На «послушных» моделях работает, полной гарантии нет.
- **Forecast параметры экспертные, не MLE.** μ/θ/σ — bank consensus + Kilian + regime-history vol, snapshot `as_of: 2026-05-08`. `base`/`bull` на 12m имеют MAPE 30-50% в backtest на 5y — параметры под текущий 2026-shock equilibrium, не universal через все regimes (intentional design choice).
- **Корпус заморожен на v2.0.0.** Time-aware retrieval и индексация новых отчётов отвергнуты в roadmap. Свежесть — через web-search. Газпром МСФО недоступен post-2022, используем РСБУ.

## 5. Возможные улучшения

В порядке приоритета (адаптировано из [roadmap-v2.1.md](roadmap-v2.1.md)):

1. **Numeric grounding (Track D4).** Two-tier regex+LLM validator для чисел в ответе — закрывает анти-галлюцинацию на числах, что для аналитика критичнее всего.
2. **Conflict resolution.** Детекция и явное озвучивание расхождений между RAG-цифрой и web-цифрой на тех же сущностях — потенциально killer-feature для senior-analyst persona.
3. **Custom scenarios для forecast.** Сейчас три fixed preset (`bear`/`base`/`bull`). API `ScenarioParams(flags=…)` уже реализован, нужно подключить runtime detection для произвольных комбинаций драйверов.
4. **Per-snapshot calibration overlay для OU.** Per-regime overlay (PRE_2022 / WAR_SHOCK / CAP_NORMALIZATION) снизит MAPE на исторических периодах с текущих 30-50% на 12m.
5. **Self-hosted Langfuse + reasoning summary в UI.** Cloud free tier — для демо; для Sber-инфраструктуры нужен self-host (privacy). Параллельно — expandable-блок «Источники и логика» под ответом.

## 6. Метрики качества

E2E eval: 100 диалогов, 5 категорий ТЗ §4.6 + multi-tool + adversarial + hedging. Метрики: **success_rate** (агент ответил, не упал), **citation_rate** (валидная цитата), **structure_score** (TL;DR + цифры + цитаты + диапазон для price), **refusal_correctness** (корректный отказ на out-of-scope).

| Метрика | v2.0.0 baseline (100 диалогов) | v2.3.5 (текущий) |
|---|---:|---:|
| success_rate | 0.568 | <!-- TODO: координатор подставит из docs/eval-results-v2.3.5.md (сессия D) --> |
| citation_rate | 0.181 | <!-- TODO: координатор подставит из docs/eval-results-v2.3.5.md (сессия D) --> |
| structure_score | 0.528 | <!-- TODO: координатор подставит из docs/eval-results-v2.3.5.md (сессия D) --> |
| refusal_correctness | 0.947 | <!-- TODO: координатор подставит из docs/eval-results-v2.3.5.md (сессия D) --> |

Forecast — отдельный walk-forward backtest на 5y history (monthly rolling, n ≈ 33 per cell): Brent bear MAPE 6-13% (production-grade), base/bull 12m MAPE 30-50% (intentional — параметры под shock-режим). Per-regime breakdown — [ADR-0024-ou-regime-forecast §A5/A8](adr/0024-ou-regime-forecast.md).
