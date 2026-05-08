# ADR-0024 — Observability: Langfuse + JSON-trace backup

- **Дата:** 2026-05-08
- **Статус:** Принято
- **Контекст:** PR `feature/observability-langfuse` (Track F roadmap-v2.1)
- **Связано:** ADR-0014 (LangGraph subgraph — узлы которые декорируем), ADR-0021 (max_output_tokens — стоимость считается из usage), ADR-0007 (LLM провайдеры — для каких моделей считать cost)

## Контекст и проблема

К v2.0.0 у агента нет server-side observability. Логи `logger.warning/info` пишутся локально, нет:

- Цепочки tool-вызовов одного запроса (видим отдельные логи, не граф).
- Cost / latency метрик per узел.
- Способа показать оценщику-аналитику Сбера «вот так работает агент в реальности».

Для production-ready заявки и для отчёта §4.5 нужен трейсинг. Параллельно — offline backup (JSON), чтобы оценщик в demo-режиме без аккаунта Langfuse мог посмотреть trace.

## Решение

**Langfuse Cloud free tier** + **параллельный JSON-trace** в `metrics/runs/<ts>/trace.jsonl`.

### Cloud vs self-hosted

Выбран Cloud:
- Скорость dev: 5 мин регистрация vs 1+ день self-hosted (postgres+redis+clickhouse+nginx). При дедлайне 4 дня (см. roadmap-v2.1) и параллельных треках (A, D) — критично.
- Privacy gain self-hosted ≈ 0 для нашего кейса: корпус публичный (OPEC, IEA, Bruegel), запросы аналитика не sensitive.
- Free tier 50k observations/мес — на смок (5 узлов × 50 диалогов = 250 obs) c гигантским запасом.
- Self-hosted на Timeweb VDS — кандидат в roadmap v2.2+, если Сбер потребует privacy.

Доступность `cloud.langfuse.com` из РФ проверена 2026-05-08: HTTP/2 200.

### Demo-режим

Feature-flag `LANGFUSE_ENABLED=false` отключает Langfuse SDK (lazy import), JSON-trace продолжает писаться. Оценщик при автономном запуске видит файлы `metrics/runs/<ts>/trace.jsonl`, но не наш Langfuse-проект. В отчёте §4.5 — скриншоты Langfuse UI как доказательство production-ready.

В нашем production deploy на Timeweb — `LANGFUSE_ENABLED=true` с нашими ключами, оценщик видит UI через ссылку (по запросу).

### Архитектура

```
nefteboros/observability/
  __init__.py     # observe + traced_tool декораторы, log_llm_usage,
                  # start_trace / end_trace
  tracer.py       # JSONL writer + Langfuse SDK lazy init, contextvars stack
  cost.py         # COST_RATES для kimi-k2p6/GigaChat + fallback в ouroboros.pricing

nefteboros/graphs/analyst_graph.py
  build_analyst_graph()        — wrap каждого узла в `observe(name=...)` при `add_node`.
  invoke_with_trace(graph, s)  — обёртка для прямого вызова графа из CLI/eval.

skills/neftegaz_analyst/plugin.py
  @traced_tool(name="analyst_query") _tool_analyst_query
  @traced_tool(name="rag_search")    _tool_rag_search
  @traced_tool(name="web_search")    _tool_web_search
```

Декораторы графа навешиваются **в builder'е** — файлы `nefteboros/graphs/nodes/*.py` остаются доменными. Tool entry points в `plugin.py` декорируются `@traced_tool` — это **e2e trace на каждый tool вызов** от Ouroboros agent loop'а до возврата JSON. Единственное вмешательство в узлы графа — `log_llm_usage(usage)` в `synthesize._call_llm` после `chat_async` (3 строки), и аналогично в `llm_disambiguate` после `chat.ainvoke` (только когда fallback-путь, см. §«Known limitations»).

### Trace lifecycle (один user-request = один trace)

**Цель:** агент в Ouroboros loop'е может за один user-request дёрнуть несколько tools (например `rag_search` → `analyst_query` → `web_search`). Все эти вызовы должны попасть в **один** trace в Langfuse — иначе оценщик видит фрагменты вместо целого запроса.

**Механизм:** Ouroboros вызывает tool handler как `handler(ctx, **args)`, где `ctx: ToolContext` (см. `ouroboros.tools.registry.ToolContext`) общий для всех tool вызовов одного task'а. Используем `ctx.task_id` (fallback `current_chat_id`) как ключ для группировки.

```
Ouroboros agent loop  (one user-request, one task_id "T_42")
   │
   ├─ tool_call("rag_search", args)
   │     handler(ctx, query=...)     ── ctx.task_id = "T_42"
   │       └─ traced_tool: get_or_create_trace_for_request("task:T_42")
   │             │  not in registry → create new Langfuse root span
   │             └─ store: registry["task:T_42"] = trace
   │       └─ child span "rag_search" → trace
   │
   ├─ tool_call("analyst_query", args)
   │     handler(ctx, query=...)     ── тот же ctx.task_id
   │       └─ traced_tool: get_or_create — found existing trace ✓
   │       └─ child span "analyst_query" → graph.ainvoke
   │            ├─ child "classify_intent"   (через _current_trace contextvar)
   │            ├─ child "forecast_call"
   │            ├─ child "synthesize"        (as_type=generation, cost/tokens)
   │            └─ child "validate_citations"
   │
   └─ tool_call("web_search", args)
         handler(ctx, query=...)     ── тот же task_id
         └─ child span "web_search" в том же trace

→ В Langfuse: один root observation с 7 child spans, total_cost = sum, total_latency = wall.
```

**Регистрация и закрытие trace:**

- `tracer._request_traces: dict[str, Trace]` — registry активных traces по `task_id`/`chat_id`. Lock'ом защищён.
- **Открытие**: первый tool вызов с данным request_id создаёт trace через `start_trace`, кладёт в registry.
- **Переиспользование**: последующие tools находят trace в registry, добавляют child observations через `trace_context={"trace_id": existing}`.
- **Закрытие — три пути**:
  1. **Явный** через `tracer.close_trace_for_request(request_id, answer=...)` — если внешний код (loop hook) знает что user-request завершён.
  2. **TTL** — 10 минут без новых tool вызовов → cleanup при следующем `get_or_create`. Защита от утечек если task завершился без сигнала.
  3. **`atexit`** — все активные traces закрываются + `langfuse.flush()` при завершении процесса. Гарантирует доставку.

**Legacy fallback** (вызов без `ctx`, e.g., из CLI / eval scripts): `_extract_request_id` возвращает None → trace **не регистрируется** → закрывается **сразу** после tool вызова (старое поведение, один tool = один trace). Это совместимо с прямым `python -m scripts.eval.eval_e2e`.

**`invoke_with_trace(graph, state)`** в `analyst_graph.py` остаётся для прямого вызова графа без Ouroboros tool dispatcher — открывает trace на один graph.ainvoke (legacy путь).

### Сигнатура tool handlers

```python
@traced_tool(name="analyst_query")
def _tool_analyst_query(ctx: Any = None, *, query: str = "") -> str:
    ...
```

Ouroboros сначала пробует `handler(self._ctx, **args)`. Если handler принимает `ctx` — получит ToolContext. `ctx=None` default поддерживает legacy `handler(**args)` fallback при TypeError.

### JSON-trace формат

См. `nefteboros/observability/tracer.py` модель `Span` / `Trace`. Один файл `<run_dir>/trace.jsonl`, JSON-line, два типа строк (`kind: span|trace`):

```jsonl
{"kind":"span","ts":"2026-05-08T...","trace_id":"<uuid>","span_id":1,"parent_span_id":null,"node":"classify_intent","status":"ok","latency_ms":3,"input":{"state_keys":[...]},"output":{"keys":["intent"]}}
{"kind":"span","ts":"...","trace_id":"<uuid>","span_id":4,"node":"synthesize","status":"ok","latency_ms":2100,"model":"kimi-k2p6","provider":"hydra","prompt_tokens":1450,"completion_tokens":340,"cost_usd":0.00078,"output":{"keys":["synthesis","citations"]}}
{"kind":"trace","ts":"...","trace_id":"<uuid>","query":"...","total_latency_ms":6307,"total_cost_usd":0.00078,"span_count":5,"status":"ok"}
```

Поля:
- `status: ok | error | skipped` — узел отработал / упал / пропущен (для `llm_disambiguate` когда rule-based уже классифицировал).
- При `status=error` — `error: {type, message}` (message truncated >500 chars).
- `parent_span_id` — null в v2.1 (плоский граф); поле зарезервировано под вложенные узлы (synthesize → tool call) в integration PR'ах.
- `cost_usd: null` ≠ `cost_usd: 0.0` — null означает «cost неизвестен» (модель не в COST_RATES и ouroboros не знает), 0.0 — «узел без LLM, точно бесплатно».
- `input` / `output` — compact preview; full text — в Langfuse UI. Truncate threshold 2KB → JSON-line ≤ ~5KB → POSIX `O_APPEND` atomic <PIPE_BUF (4KB) ≈ выполняется. Assertion `tracer.py:TRUNCATE_THRESHOLD` — future-proof guard.

### Cost calculation

Иерархия (`nefteboros/observability/cost.py`):

1. **Наши `COST_RATES`** — захардкожены ставки для моделей нашего стека: kimi-k2p6, GigaChat-2-Max, GigaChat-Max + остальные Hydra-модели на случай экспериментов.
2. **`ouroboros.pricing.estimate_cost`** — fallback для OpenRouter-style моделей.
3. **None** — если ставка не найдена.

Ставки в USD per 1M tokens, обновляются руками. Источники:
- Hydra: https://hydragpt.ru/pricing
- GigaChat: https://developers.sber.ru/docs/ru/gigachat/api/tariffs (пересчитано из ₽ по курсу 92 ₽/$).

Для cost-aware дашборда в roadmap v2.2 рассмотреть выгрузку в YAML / запрос провайдеров.

### Координация с Track D

Env-переменная `OBSERVABILITY_RUN_DIR`:
- Если выставлена — JSON-trace пишется туда.
- Если нет — `metrics/runs/<utcnow_iso>/trace.jsonl`.

D2 baseline-run обязан перед запуском выставлять `OBSERVABILITY_RUN_DIR=$(mktemp -d ./metrics/runs/<run_ts>)`, чтобы `e2e-baseline.json` и `trace.jsonl` лежали в одной папке. Контракт описан в `docs/roadmap-v2.1.md` Track D.

### Failure modes — graceful degradation

Все ошибки наблюдения **никогда** не попадают в финальный ответ агента или UI. Только в Python `logging`:

- `langfuse` SDK не установлен → lazy import → warning, JSON-trace продолжает.
- Langfuse Cloud недоступен (network, RKN, плохой ключ) → init catches → флаг форсится в false на уровне процесса, JSON-trace продолжает.
- JSONL файл не открывается (read-only fs) → warning, span'ы скипаются.
- `log_llm_usage` вызван вне span контекста (unit-test) → debug-лог, no-op.

## Альтернативы

- **LangSmith.** Отвергнуто (см. roadmap §«Что отвергли и почему»): лочит на LangChain, мы предпочитаем self-hosted-able решение.
- **Свой logger в JSONL без UI.** Отвергнуто: dashboard сами строить — потеря времени, для оценщика-аналитика UI важнее.
- **LangChain Langfuse `CallbackHandler` для всего стека.** Отвергнуто:
  - Дублирование span'ов: `@observe` на узле + Callback на чате → два span'а в Langfuse за один LLM-вызов.
  - Несимметрия с `synthesize` (там через `ouroboros.LLMClient`, не LangChain) → два разных пути логирования = две места поломки.
- **Self-hosted Langfuse на demo VDS.** Отвергнуто на v2.1: docker-стек langfuse требует postgres + redis + clickhouse + nginx, на 4 дня до сдачи это рискованный таймсинк. Кандидат roadmap v2.2+.
- **Public dashboard в Langfuse Cloud.** Отвергнуто: смешает наши тестовые запуски с запросами оценщика, не контролируем доступ.
- **Ad-hoc trace в `observe`-декораторе** (если top-level trace не открыт). Отвергнуто после саморазгрома: каждый из 5 узлов в graph.ainvoke создавал бы СВОЙ trace вместо одного на запрос. Решение — `invoke_with_trace` обёртка для entry-points.

## Known limitations

1. **`llm_disambiguate` cost = null на fast-path.** LangChain `chat.with_structured_output(_LLMIntent)` теряет `usage_metadata` в большинстве реализаций — мы получаем уже распарсенный объект без метаданных. На fallback-пути (NotImplementedError/AttributeError) `usage_metadata` сохраняется через `chat.ainvoke()`. **Не лезем внутрь узла менять fast-path** — соглашаемся на null cost для GigaChat-2-Max в этом узле. Latency меряется честно.
2. **Узлы `retrieve_rag` / `web_search` пока не существуют.** Они будут добавлены в integration PR'ах (Track B). При появлении — добавить в `analyst_graph.build_analyst_graph()` тем же паттерном `observe(name=...)(fn)`.
3. **TRUNCATE_THRESHOLD = 2KB.** При увеличении до 8KB атомарность `O_APPEND` нарушится — assertion на старте процесса (`tracer.py:TRUNCATE_THRESHOLD`) подскажет.
4. **Cost rates захардкожены.** При смене тарифов GigaChat / Hydra надо обновлять `COST_RATES` руками. Для смока v2.1 приемлемо, в v2.2 — выгрузка из provider config / YAML.
5. **`forecast_call` cost = 0.** Forecast tool не использует LLM (SARIMAX + GBR), cost корректно нулевой. Это отражено в JSON как отсутствие `cost_usd` в span'е (не null, потому что LLM не вызывался — null зарезервирован для «LLM-вызов был, но cost неизвестен»).

## Acceptance

- ✅ Langfuse SDK в `requirements-domain.txt`.
- ✅ `LANGFUSE_*` в `.env.example` с инструкциями.
- ✅ `nefteboros/observability/` — пакет с tracer / cost / observe / log_llm_usage / start_trace / end_trace.
- ✅ Все 5 узлов analyst_graph декорированы через wrap.
- ✅ Synthesize и llm_disambiguate логируют usage через `log_llm_usage`.
- ✅ Smoke-скрипт `scripts/smoke_observability.py` — JSON-trace путь работает (3 запроса, 11 span'ов, 3 trace summary).
- ⏳ Smoke с Langfuse UI — после получения ключей.
- ✅ Координация `OBSERVABILITY_RUN_DIR` с Track D — описано здесь.

## Дальше

После получения ключей Langfuse Cloud:
1. Записать `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` в локальный `.env`.
2. Установить `pip install langfuse>=2.0.0`.
3. Запустить smoke с `LANGFUSE_ENABLED=true`, проверить span'ы в UI.
4. Обновить статус пункта acceptance.

В roadmap v2.2+ (после сдачи):
- Self-hosted Langfuse, если Сбер потребует privacy.
- Cost rates в YAML с провайдерами + автообновление.
- Lite-trace в expandable-блок UI пользователя (см. roadmap §«Backlog В»).
