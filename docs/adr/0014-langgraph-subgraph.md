# ADR-0014 — LangGraph subgraph для аналитика

- **Дата:** 2026-05-06
- **Статус:** Принято
- **Контекст:** PR `feature/langgraph-subgraph` (minimal-graph; preceeded by саморазгром над `feature/forecast-skill` v1)
- **Связано:** ADR-0001 (LangGraph subgraph как место для disambiguation logic), ADR-0012 (forecast API), ADR-0013 §«Constraints for SKILL.md» (5 правил disambiguation, реализуем здесь как код)

## Контекст и проблема

ADR-0013 §Constraints зафиксировал 5 правил disambiguation для агента-аналитика:
1. Generic asset disambiguation («нефть»→brent; «нефть РФ-контекст»→brent+urals+urals_minfin_blend; «WTI»→wti; «газ»→henry_hub+ttf).
2. Unknown asset → web-search → semantic family → proxy + disclaimer.
3. Horizon limits (`<1m` polite refuse; `>=18m` direct redirect к сценариям RAG).
4. Derived asset method consistency (для urals/espo/blend method обязателен).
5. Russian gas direct pricing → explicit refusal с redirect к TTF/GAZP/RAG.

Изначально (PR `feature/forecast-skill` v1) планировалось разместить эти правила в SKILL.md systemprompt. Саморазгром выявил три ловушки:

- **Body SKILL.md не видит LLM-агент.** В Ouroboros body — review-pack для трёх AI-ревьюеров `review_skill` pipeline + catalog metadata для Skills UI. LLM-агент в loop читает только `tool.description` + Ouroboros system prompt. Размещение правил в body = правила не доходят до агента.
- **`tool.description` даёт только best-effort enforcement.** LLM может прочитать правило и применить — может проигнорировать. Нет deterministic gate, нет unit-тестов, нет measurable behavior.
- **Двойной tool surface (`oil_gas_forecast` + `analyst_query`)** создал бы двусмысленность для агента: «direct numerical» vs «complex analyst-grade» — выбор делает LLM, и снова best-effort.

Нужен слой, где правила disambiguation:
- реализованы как **код** (rule-based regex'ы), не текст;
- покрыты **unit-тестами** 1-в-1 со списком из ADR-0013;
- **routing** между источниками (forecast / RAG / web) — explicit, не «LLM решит».

## Решение

LangGraph subgraph `nefteboros/graphs/analyst_graph.py` — детерминированный orchestrator между Ouroboros tool dispatch'ем и доменной логикой:

```
                      ┌─────────────────┐
                      │ classify_intent │  rule-based (regex по 5 правилам ADR-0013)
                      └────────┬────────┘
                               │
                  conditional edges
            ┌──────────────────┼──────────────────┐
            │                  │                  │
   forecast_simple /   russian_gas_refusal   out_of_scope
   forecast_with_context        │                  │
            │                  │                  │
            ▼                  ▼                  ▼
     ┌──────────────┐    ┌──────────────────────────┐
     │ forecast_call│    │ synthesize  (refusal text)│
     └──────┬───────┘    └────────────┬─────────────┘
            │                         │
            ▼                         │
     ┌──────────────┐                 │
     │  synthesize  │                 │
     └──────┬───────┘                 │
            │                         │
            └────────────┬────────────┘
                         ▼
                ┌────────────────────┐
                │ validate_citations │  light: regex pass over forecast metadata
                └────────────────────┘
                         │
                         ▼
                  GraphState (final)
```

В этом PR — **minimal-graph**: ровно 4 типа узлов (`classify_intent`, `forecast_call`, `synthesize`, `validate_citations`). Узлы для RAG / web и расширение `synthesize` под overlay — отложены в собственные PR'ы (`feature/rag-integration`, `feature/web-integration`) со своими pydantic-контрактами.

## Что в этом PR

```
nefteboros/graphs/
├── __init__.py
├── state.py                          # GraphState, Intent, IntentType, Citation
├── intents.py                        # classify_intent rule-based + horizon parser
├── analyst_graph.py                  # StateGraph wiring + conditional edges
└── nodes/
    ├── __init__.py
    ├── forecast.py                   # multi-asset forecast() invocation, lazy import
    ├── synthesize.py                 # LLM via ouroboros.llm router
    └── validate.py                   # light regex citations pass

nefteboros/prompts/
├── system_analyst.md                 # роль аналитика (используется synthesize node)
└── synthesize_forecast_only.md       # template для текущего scope (без RAG/web overlay)

tests/
├── test_intent_classifier.py         # 12+ unit-тестов на 5 правил ADR-0013 + edge-кейсы
└── test_graph_smoke.py               # e2e с monkeypatch'ами на forecast() и LLM

docs/adr/0014-langgraph-subgraph.md   # этот документ
docs/changelog/2026-05-06-langgraph-subgraph.md
requirements-domain.txt               # +langgraph>=0.2.0
```

## Аргументация — главные неочевидные решения

### Почему graph-first, а не skill-first

**Альтернатива:** тонкий Ouroboros tool `oil_gas_forecast` обёрткой над `forecast()`, правила disambiguation в `tool.description` (best-effort через LLM).

**Минусы skill-first:**
- Правила best-effort. Конкретный сценарий: запрос «сколько Минфин закладывает в бюджет 2026?» — LLM **может** вызвать только brent (пропустить urals/blend), **может** вообще не вызвать forecast, **может** вызвать неверный horizon. Никто не гарантирует, никто не ловит — пока golden-eval не появится.
- Нет unit-тестов на правила. Регрессия незаметна.
- Двойной tool surface при появлении `analyst_query` (graph-tool) — LLM выбирает между ними по description'ам, снова best-effort.

**Плюсы graph-first:**
- Правила в коде, deterministic. classify_intent с regex'ами не «может пропустить» Минфин.
- Unit-тесты 1-в-1 со списком ADR-0013.
- Узлы для RAG/web заполнятся в их PR'ах **без правки entry point'а** — добавятся новые edges, classify_intent расширится новыми intent.type. Skill (когда появится) останется тонким.
- Один tool в будущем (`analyst_query`) — нет двусмысленности «direct vs complex».

**Минусы graph-first:**
- LangGraph dependency.
- До интеграции с RAG/web `synthesize` без overlay'я → ответы тонкие. Это **honest** — мы говорим в ответе «full hybrid awaiting RAG/web PRs».
- Multi-asset forecast (РФ-контекст: 3 вызова `forecast()`) — sequential в текущем виде, latency не оптимальна. Mitigation в integration PR: `asyncio.gather`.

### Почему rule-based classify_intent, а не LLM-classify

- **Testability**: regex'ы покрываются unit-тестами с фиксированным IO; LLM-classify требует golden-eval с manual labels и LLM-вызовом per check.
- **Latency**: 0 LLM-вызовов на классификацию. На simple запросах cycle = только synthesize-call.
- **Cost**: дешевле в production, особенно при batch'ах demo.
- **Reproducibility**: deterministic — debug disambiguation logic пошагово, не реверсить ответы провайдера.

**Минус**: rule-based падает на формулировках вне keyword-набора. Mitigation: классификатор по умолчанию выдаёт `out_of_scope` с явным сообщением «не покрыто текущим набором правил, переформулируйте» — это лучше, чем silently дать неверный intent.

### Почему conditional edges, не explicit router узел

LangGraph поддерживает predicate-based edges через `add_conditional_edges(source, condition_fn, mapping)`. Это даёт ту же routing-семантику без отдельного узла. Меньше code surface, меньше state mutations. classify_intent определяет `intent.type` в state, edges смотрят на это поле и роутят в forecast_call / synthesize (refusal) / etc.

### Почему `synthesize` через `ouroboros.llm` router, а не langchain LLM

- Ouroboros форк уже имеет multi-provider router (`ouroboros/llm.py`) — GigaChat / OpenAI-compatible (HydraGPT) / Cloud.ru.
- Дублирование LangChain LLM stack добавит зависимость без выгоды.
- LangGraph node — простая async функция, может звать что угодно. Использовать встроенный langchain-обвязчик не обязательно.

### Multi-asset forecast в одном узле

`GraphState.forecast_results: list[ForecastResult | ForecastRefusal]` — список, не один объект. Для intent.type=`forecast_with_context` (РФ-контекст: brent + urals + urals_minfin_blend) узел `forecast_call` дёргает `forecast()` для каждого актива из `intent.forecast_assets`, складывает все результаты в список. `synthesize` получает список и кратко представляет каждый.

В этом PR — sequential. Параллелизация (`asyncio.gather`) — отложена в integration PR (когда узлы RAG/web запараллелены с forecast'ом, общий performance-проход имеет смысл).

### Почему `validate_citations` — light pass пока

RAG/web ещё нет → цитировать в synthesis нечего, кроме **forecast metadata** (asset, method, horizon, ADR-0012 модель-spec, observed coverage из бектеста). Light pass проверяет regex pattern `[<source>]` в synthesis + наличие ссылки на `ADR-0012` для самой модели. Расширится в integration PR'ах: regex match RAG chunks по `source_title` + page, hallucination flag для несуществующих ссылок.

## Последствия

**Плюсы:**
- Deterministic enforcement правил disambiguation. Закрывает deficit, который был ключевой претензией к skill-first плану.
- Расширяемость: новые источники (RAG/web/macro) добавляются как узлы графа без переписывания entry point'а или skill wrapper'а.
- Testability: rule-based classify_intent + unit-тесты — основа для regression detection.
- Ясная архитектура для собеседования / демо в Сбере: «вот граф, вот узлы, вот тесты на каждое правило» — вместо «LLM сама как-нибудь решит».

**Минусы / риски:**
- Зависимость от LangGraph (~5 MB +deps). Для нашего форка — приемлемо, не блокирует ничего.
- `synthesize` без RAG/web overlay'я в текущем виде → ответы «тонкие» до появления соответствующих PR'ов. **Это honest** — мы помечаем pending в самом ответе.
- Multi-asset sequential forecast → latency на больших горизонтах (особенно 12m с rolling backtest). Mitigation: `asyncio.gather` в integration PR.
- Rule-based classify хрупок к формулировкам вне keyword-набора. Mitigation: явный `out_of_scope` ответ + расширение rules при появлении паттернов.

## Что НЕ в этом PR (явно)

- **`nefteboros/contracts/rag.py`** — pydantic Chunk/RetrievalResult schema. Будет в `feature/rag-integration` со своим контрактом, диктуемым реальной retrieval-логикой.
- **`nodes/rag.py` / `nodes/web.py`** — узлы. Появятся в `feature/rag-integration` / `feature/web-integration`.
- **synthesize_with_overlay** — расширение текущего `synthesize` под RAG/web overlay. Тот же PR, что узлы.
- **`skills/neftegaz_analyst`** — Ouroboros wrapper-tool над graph. После integration — последующий тонкий skill PR (один tool `analyst_query`, ~50 LOC, обёртка над `analyst_graph.ainvoke`).
- **Правка system prompt'а Ouroboros форка** (роль «Старший аналитик» в `prompts/`) — отдельный PR `feature/system-prompt-analyst`. До его merge skill хоть и зарегистрирован, не вызывается автоматически.
- **5 demo-сценариев** ТЗ §4.6 — отдельный PR `feature/demo-scenarios` после integration.
- **Markov-Switching / GARCH / Baumeister-Kilian** — PR3 (см. ADR-0012 §«Что НЕ в PR1»).

## Альтернативы рассмотренные

- **skill-only без graph** (исходный план PR2 `feature/forecast-skill`): отвергнуто — deficit правил disambiguation; см. ADR-0013 §Constraints + saved discussion.
- **LLM-based classify_intent**: отвергнуто — testability, latency, cost. Может пересмотреть в PR3, если rule-based выдаст плохое coverage на golden-eval.
- **Explicit `router` узел между classify и forecast/synthesize**: отвергнуто — conditional edges LangGraph покрывают эту функцию без code surface.
- **`synthesize` через `langchain.ChatOpenAI`**: отвергнуто — дублирование LLM stack с `ouroboros.llm`.
- **Параллельное multi-asset forecast в graph (`asyncio.gather` сразу)**: отложено в integration PR — sequential для PR2 проще, перформанс не блокер на текущих горизонтах.
- **Контракт `Chunk` сейчас, RAG-сессия импортирует**: отвергнуто (см. discussion) — преждевременное проектирование, нарушает YAGNI; RAG-сессия родит свой контракт под реальную retrieval-логику.

## Ссылки

- [docs/adr/0001-fork-ouroboros.md](0001-fork-ouroboros.md) §«Логика приоритизации источников» — место graph внутри analyst_query tool'а
- [docs/adr/0012-price-tools.md](0012-price-tools.md) — модель `forecast()`, available methods и assets
- [docs/adr/0013-hybrid-forecasting.md](0013-hybrid-forecasting.md) §«Constraints for SKILL.md» — 5 правил disambiguation, теперь реализованных как rule-based код
- [docs/architecture.md](../architecture.md) — graph внутри `analyst_query` tool'а
- ТЗ §2.4 [docs/tz/original.md](../tz/original.md) — логика приоритизации источников (RAG > web > forecast для structural; обратно для текущих)
