# ADR-0016 — Тонкий skill `neftegaz_analyst` поверх analyst graph

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/forecast-skill` — финальная интеграция analyst pipeline в Ouroboros loop.
- **Связано:** ADR-0001 (форк Ouroboros), ADR-0014 (LangGraph subgraph), ADR-0015 (LLM-disambiguate hybrid), `docs/experiments/intent_classifier.md` (golden eval 0.98 type accuracy).

## Контекст и проблема

После PR'ов #8 (graph baseline), #9 (LLM disambiguate) и #10 (golden eval) у нас есть analyst graph с 0.98 type accuracy и закрытым deficit'ом disambiguation. Но **граф невидим Ouroboros loop'у** — он живёт изолированно в `nefteboros/graphs/analyst_graph.py`, вызывается только напрямую через `analyst_graph.ainvoke()`. Из Ouroboros UI / chat этот pipeline недоступен. Ни один tool, ни один skill не expose'ит граф наружу.

Это блокер production-deploy на Timeweb: пользователь, открывающий Ouroboros UI, не может задать аналитический вопрос — граф не зарегистрирован как tool.

В первоначальном плане (PR1 `feature/forecast-skill`, до саморазгрома) skill должен был содержать **два tool'а**: `oil_gas_forecast` (прямая обёртка над `forecast()`) + `analyst_query` (граф). После переоценки в ADR-0014 graph-first архитектуры — двойной tool surface не нужен: routing «direct vs complex» делает граф через `classify_intent` → conditional edges. Внешне нужен **один entry-point**.

## Решение

Skill `skills/neftegaz_analyst/` экспортирует **ровно один tool** `analyst_query` через `PluginAPI v1`. Tool — тонкая обёртка над `build_analyst_graph().ainvoke(GraphState(query))`:

```python
def _tool_analyst_query(*, query: str = "") -> str:
    # validate query
    # lazy import nefteboros.graphs.analyst_graph
    # graph.ainvoke(GraphState(query)) → final state dict
    # serialize {synthesis, intent, citations, validation_warnings, forecast_errors}
    # return JSON
```

Plus один route `GET /api/extensions/neftegaz_analyst/health` — lightweight liveness probe (без вызова графа).

Permissions строго минимальные: `[tool, route]`. Без `widget` (нет UI tab — добавится в `feature/analyst-ui-widget`), без `read_settings` (env'ы читаются под graph layer'ом, не PluginAPI), без `subprocess` / `fs` / `iframe_raw`.

Type — `extension`. Manifest schema_version 1 (`SKILL_MANIFEST_SCHEMA_VERSION` в `ouroboros/contracts/skill_manifest.py`).

## Что в этом PR

```
skills/neftegaz_analyst/SKILL.md       # переписан полностью — frontmatter + body
                                       #   (review-pack для humans + ревьюеров,
                                       #    НЕ systemprompt — это поняли в ADR-0014)
skills/neftegaz_analyst/plugin.py      # переписан — _tool_analyst_query + _route_health
                                       #   + register(api), lazy import графа

tests/test_neftegaz_skill_smoke.py     # NEW — 4 теста:
                                       #   discover_skills находит skill,
                                       #   manifest валиден без warnings,
                                       #   register(api) через capture-mock,
                                       #   _tool_analyst_query с mock graph

docs/adr/0016-forecast-skill.md        # этот документ
docs/changelog/2026-05-07-forecast-skill.md
```

`requirements-domain.txt` не правится — все нужные deps уже в проекте (langgraph, langchain-gigachat, pandas, numpy, statsmodels, ...).

## Аргументация — главные неочевидные решения

### Почему **один** tool, а не два

Изначальный план разделял `oil_gas_forecast(asset, horizon, method?)` (прямой forecast call) и `analyst_query(query)` (через граф). После ADR-0014 — двойной surface не нужен:

- Routing «direct numerical vs complex analyst» уже происходит **внутри графа** через `classify_intent` + conditional edges. Forecast intent → `forecast_call` → `synthesize`. Refusal intent → `synthesize` без LLM. LLM-zone → `llm_disambiguate` → возврат к routing.
- Двойной surface на Ouroboros loop'е заставляет агента-LLM выбирать между tool'ами по description'ам — снова best-effort, та же ловушка, что мы решали в graph-first переходе.
- Один tool = одна точка инструкции в `tool.description` = меньше места для LLM-ошибки выбора.

### Почему `tool.description` — расширенная инструкция, а не короткое имя

`tool.description` — единственное место, где LLM при tool selection видит **наши правила** (когда вызывать tool, на что не вызывать). Ouroboros loop передаёт этот текст вместе с tool spec'ом провайдеру. SKILL.md body — review-pack, **не доходит** до агента (см. ADR-0014 §«Большая бомба I» из обсуждения).

Описание содержит:
- Назначение и активы (Brent/WTI/Urals/...).
- Когда вызывать (forecast/budget/scenarios).
- Когда **НЕ** вызывать (погода, биткоин, общее общение).
- Возврат — JSON.

Длина ~600 chars — в пределах OpenAI/Anthropic tool spec лимитов.

### Почему lazy import графа

`analyst_graph` тащит:
- LangGraph (compiled state graph).
- langchain-gigachat (LLM client для disambiguate).
- pandas/numpy/statsmodels/sklearn/yfinance (через `forecast_call` узел при первом вызове).
- ouroboros.llm для synthesize.

Eager import в `plugin.py` сломал бы:
- **Ouroboros CI** — там минимальный `requirements.txt` без domain stack. discover_skills падал бы на нашем skill'е.
- **Cold-start launcher** — на маленькой машине импорт ~1.5 сек, юзер не должен ждать загрузки `forecast.py` если skill не вызывается.

Решение: `from nefteboros.graphs.analyst_graph import ...` **внутри** `_tool_analyst_query`, не на module-level. `register(api)` остаётся лёгким.

### Почему `asyncio.run(graph.ainvoke(...))` синхронно

PluginAPI v1 (`Callable[..., str]`) — **synchronous** handler. Граф async (`ainvoke`). Стандартное решение — `asyncio.run()`. Для каждого tool-call создаётся новый event loop — приемлемо: вызов forecast() занимает несколько секунд, накладные затраты на loop creation ничтожны (<1ms).

Альтернатива — bridge на async PluginAPI handler. Это требовало бы расширения PluginAPI v1 (изменение frozen ABI). Не оправдано для одного skill'а.

### Почему `_MAX_QUERY_CHARS = 2000`

Защита от случайного pasta-bombing'а пользователем (paste огромного текста в chat). 2000 символов — достаточно для любого нефтегазового вопроса с контекстом, но мала для DDoS-vector'а через большие prompt'ы. Граф всё равно не воспринимает length меньше 5-100 chars.

### Permissions: только `[tool, route]`

- **`tool`** — `register_tool(analyst_query)`.
- **`route`** — `register_route(health)`.
- **Не `widget`** — UI tab не нужен в этом PR (skill используется через chat). Виджеты — `feature/analyst-ui-widget`.
- **Не `read_settings`** — env'ы (`OUROBOROS_MODEL`, `GIGACHAT_*`) читаются `os.environ` под graph layer'ом (`ouroboros.llm`, `nefteboros.llm.gigachat`). PluginAPI `get_settings` не нужен.
- **Не `subprocess` / `fs` / `iframe_raw`** — handler не дёргает shell, не пишет на диск (forecast cache — управляет `forecast.cache.py`, не plugin), не рендерит iframe'ы.

Минимум surface = легче review pipeline (3 AI-ревьюера в `review_skill` дадут «pass» быстрее).

## Последствия

**Плюсы:**
- **Закрытый блокер production-deploy**: после этого PR analyst graph доступен через Ouroboros UI / chat.
- Один tool surface — нет двусмысленности «direct vs complex», LLM не теряется в выборе.
- Lazy import графа — Ouroboros CI без domain deps не падает.
- Skill готов к review pipeline + enabling через UI / CLI.
- Открыт путь к demo-сценариям ТЗ §4.6 (для них нужен callable tool, который теперь есть).

**Минусы / риски:**
- **Skill не функционален без правки системного промпта Ouroboros** (`prompts/`). Default systemprompt («I am self-modifying AI») может не выбрать `analyst_query` на нефтегазовые запросы. Это **отдельный** PR `feature/system-prompt-analyst`.
- **Latency cold-start**: первый вызов после Ouroboros restart — 5-15 секунд (lazy import графа + LLM round-trip в synthesize). Последующие — 2-5 сек.
- **Synthesize без RAG/web overlay** — ответы тонкие до `feature/rag-integration`.
- **Phase 4 review gate**: после deploy skill требует `review_skill` (триметодельное ревью) + `enable_skill`. На сервере — manual через UI или CLI. Это operational нюанс, не блокер.

**Митигации:**
- Документировать в README + production-deploy workflow «после deploy: open UI → Skills → review + enable neftegaz_analyst».
- В `_tool_analyst_query` graceful degradation на любых ошибках — JSON с `error`-полем, не raise.
- Внутренние error'ы graph (forecast() unavailable, LLM down) логируются + попадают в `forecast_errors` поле ответа — пользователь видит частичный результат.

## Что НЕ в этом PR (явно)

- **`feature/system-prompt-analyst`** — правка `prompts/system.md` форка под роль «Старший аналитик». Без неё skill зарегистрирован, но автоматически не вызывается агентом. Отдельный PR.
- **`feature/analyst-ui-widget`** — отдельный UI tab (Widgets page) для analyst pipeline. Сейчас skill доступен только через chat и `/api/extensions/.../health`. Виджет — отдельный PR с `register_ui_tab`.
- **`feature/rag-integration`** — узлы `rag_retrieve` + `web_search` + расширение synthesize_with_overlay. Без них synthesis тонкий (только base-case).
- **5 demo-сценариев** ТЗ §4.6 — отдельный PR `feature/demo-scenarios` (golden questions для ручной проверки + screenshots для отчёта).
- **Async PluginAPI handler** — расширение PluginAPI v1 для native async tool'ов. Не оправдано для одного skill'а; `asyncio.run()` справляется.
- **Метрики per-call (latency / token usage)** в state.metadata — будущая observability через `langfuse` или аналог. Сейчас через `logger.info`.

## Альтернативы рассмотренные

- **Два tool'а (`oil_gas_forecast` + `analyst_query`)**: отвергнуто после ADR-0014 — routing уже в графе, дублирование на уровне Ouroboros tool'ов создаёт ту же проблему «LLM выбирает между двумя» (best-effort, не deterministic).
- **Eager import графа в `plugin.py`**: отвергнуто — Ouroboros CI без domain deps (`requirements.txt`, не `requirements-domain.txt`) падал бы на parse-time.
- **`PluginAPI` async handler**: отвергнуто — ABI v1 frozen, расширение требует SKILL_MANIFEST_SCHEMA_VERSION bump. `asyncio.run()` достаточен для single-call.
- **`with_streaming` для long-running synthesize**: отвергнуто — current PluginAPI v1 не поддерживает streaming. Pipeline укладывается в 2-15 секунд (timeout_sec=120 — щедрый запас).
- **`register_ui_tab` в этом PR**: отвергнуто — ui tab расширяет review pack (нужны permissions=[widget], валидируется declarative widget schema). Объём PR растёт; UI-виджет — отдельный feature.
- **Permissions `[net]`**: отвергнуто — `register_tool` / `register_route` не требуют `net` (это отдельная permission для plugin'ов, делающих fetch'и). Network-actions графа happen внутри `forecast()` / LLM call'ов, ответственность которых лежит на их библиотечных уровнях, не на skill'е.

## Ссылки

- ADR-0001: [docs/adr/0001-fork-ouroboros.md](0001-fork-ouroboros.md) — место graph внутри analyst_query tool'а.
- ADR-0014: [docs/adr/0014-langgraph-subgraph.md](0014-langgraph-subgraph.md) — minimal-graph baseline.
- ADR-0015: [docs/adr/0015-llm-disambiguate.md](0015-llm-disambiguate.md) — hybrid disambiguation.
- ADR-0012: [docs/adr/0012-price-tools.md](0012-price-tools.md) — `forecast()` API.
- Эксперимент: [docs/experiments/intent_classifier.md](../experiments/intent_classifier.md) — 0.98 type accuracy на 100-датасете.
- Архитектура: [docs/architecture.md](../architecture.md) — high-level схема.
- Phase 4 review pipeline: `ouroboros/skill_review.py`, `ouroboros/extension_loader.py`, `ouroboros/contracts/plugin_api.py`.
