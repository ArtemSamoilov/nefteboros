# Changelog: feature/forecast-skill — тонкий skill `neftegaz_analyst` поверх analyst graph

- **Дата:** 2026-05-07
- **PR:** `feature/forecast-skill`
- **ADRs:** [ADR-0016 — Тонкий skill `neftegaz_analyst`](../adr/0016-forecast-skill.md)

## Задача

Закрыть блокер production-deploy: graph (PR'ы #8, #9, #10) был невидим Ouroboros loop'у — пользователь в UI не мог его вызвать. Этот PR экспортирует analyst pipeline через PluginAPI v1 как один tool `analyst_query`.

## Контекст

Изначальный PR2 `feature/forecast-skill` (до саморазгрома и rescope) планировался как 2-tool skill (`oil_gas_forecast` + `analyst_query`). После переоценки в ADR-0014 (graph-first) — двойной surface не нужен: routing «direct vs complex» делает граф через `classify_intent` + conditional edges. Outside нужен **один entry-point** `analyst_query`, что и реализовано здесь.

## Что сделано

### ADR

- `docs/adr/0016-forecast-skill.md` — обоснование thin-wrapper, lazy import графа, минимальные permissions, почему один tool а не два, почему `tool.description` (а не SKILL.md body) — реальная инструкция для агента.

### SKILL.md

`skills/neftegaz_analyst/SKILL.md` переписан полностью:
- Frontmatter: `name`, `description`, `version=0.1.0`, `type=extension`, `entry=plugin.py`, `permissions=[tool, route]`, `env_from_settings=[]`, `when_to_use`.
- Body — **review-pack для humans + AI-ревьюеров `review_skill` pipeline**: roles, surfaces (tool + route), permissions с обоснованием, архитектурная диаграмма, безопасность, связь с ADR-0014/0015/0016. Не systemprompt — это уже понятно после ADR-0014 §«Bomb I».

### plugin.py

`skills/neftegaz_analyst/plugin.py` переписан:
- `_tool_analyst_query(*, query: str)` — thin wrapper. Validate query (1-2000 chars). Lazy import `nefteboros.graphs.analyst_graph`. `asyncio.run(graph.ainvoke(GraphState(query)))`. Serialize → JSON `{synthesis, intent, citations, validation_warnings, forecast_errors}`.
- `_route_health(request)` — `GET /api/extensions/neftegaz_analyst/health`. Lightweight liveness probe без вызова графа.
- `register(api)` — `register_tool("analyst_query", ..., timeout_sec=120)` + `register_route("health", ...)`. Логирование загрузки.
- `_TOOL_DESCRIPTION` (~600 chars) — мини-systemprompt для LLM при tool selection: что делает tool, на какие вопросы вызывать, что **не** использовать (погода, биткоин, общее общение).
- Resilient: graph error / LLM error / forecast error → JSON с `error`-полем, не raise.

### Tests

`tests/test_neftegaz_skill_smoke.py` — 9 smoke-тестов:
- `test_manifest_parses_without_warnings` — `parse_skill_manifest_text` валиден без warnings.
- `test_manifest_permissions_minimal` — ровно `[route, tool]`.
- `test_manifest_no_env_from_settings` — пусто.
- `test_discover_skills_finds_neftegaz_analyst` — `discover_skills` с `repo_path=skills/` находит skill, `load_error == ""`.
- `test_register_tool_and_route` — capture-mock api, регистрация `analyst_query` (timeout 120, schema проверена) + `health` (GET).
- `test_tool_empty_query_returns_error` — `query=""` → `{"error": "query is empty"}`.
- `test_tool_too_long_query_returns_error` — 2500 chars → error JSON.
- `test_tool_invokes_graph_and_returns_json` — happy path с monkey-patched `build_analyst_graph`, проверка всех ключей выхода (synthesis, intent, citations, validation_warnings, forecast_errors).
- `test_tool_handles_graph_runtime_error` — `RuntimeError` из ainvoke → JSON с error, handler не падает.

**Итого: 77/77 passed** (53 intent_classifier + 8 graph_smoke + 7 llm_disambiguate + 9 neftegaz_skill_smoke).

## Тесты

- AST OK на новых .py.
- Все 77 тестов passed без сетевых вызовов (heavy stack — pandas/statsmodels/yfinance/langgraph/langchain-gigachat — НЕ загружается на parse + register благодаря lazy import; в tool-handler тоже мокаем `build_analyst_graph` через `monkeypatch`).
- starlette установлен в локальный venv для тестов (Ouroboros core dep, есть на сервере).

## Что НЕ в этом PR (явно)

- **`feature/system-prompt-analyst`** — правка `prompts/system.md` форка Ouroboros под роль «Старший аналитик». Без неё default systemprompt («I am self-modifying AI») не выберет `analyst_query` на нефтегазовые запросы. Это **следующий PR**.
- **`feature/analyst-ui-widget`** — отдельный UI tab (Widgets page) для analyst pipeline через `register_ui_tab`. Сейчас skill доступен только через chat и `/health`-route.
- **`feature/rag-integration`** — узлы `rag_retrieve` + `web_search` + расширение `synthesize_with_overlay`. Без них synthesis тонкий (только base-case forecast). RAG-сессия progresses параллельно (PR #11 mergeed, RAG chunking готов).
- **5 demo-сценариев ТЗ §4.6** — отдельный PR `feature/demo-scenarios` (golden questions для ручной проверки + screenshots).
- **Async PluginAPI handler** — расширение PluginAPI v1 для native async tool'ов. Не оправдано для одного skill'а; `asyncio.run()` справляется.
- **Phase 4 review pipeline** — после deploy на сервер skill требует `review_skill` (триметодельный AI review) + `enable_skill` через UI или CLI. Это **operational** workflow, не код в этом PR.

## Файлы

**Добавлено:**
- `docs/adr/0016-forecast-skill.md`
- `docs/changelog/2026-05-07-forecast-skill.md` (этот файл)
- `tests/test_neftegaz_skill_smoke.py`

**Изменено (полная перезапись placeholder'ов):**
- `skills/neftegaz_analyst/SKILL.md`
- `skills/neftegaz_analyst/plugin.py`

**Удалено:** —

**Зависимости:** не правились — все нужные deps уже в `requirements-domain.txt` (langgraph, langchain-gigachat, pydantic) и в Ouroboros core (starlette).

## Связанные документы

- ADR-0016: [docs/adr/0016-forecast-skill.md](../adr/0016-forecast-skill.md)
- ADR-0014: [docs/adr/0014-langgraph-subgraph.md](../adr/0014-langgraph-subgraph.md) — minimal-graph baseline
- ADR-0015: [docs/adr/0015-llm-disambiguate.md](../adr/0015-llm-disambiguate.md) — hybrid disambiguation
- Эксперимент: [docs/experiments/intent_classifier.md](../experiments/intent_classifier.md) — 0.98 type accuracy на 100-датасете
- Архитектура: [docs/architecture.md](../architecture.md)
- Phase 4 review pipeline: `ouroboros/skill_review.py`, `ouroboros/extension_loader.py`, `ouroboros/contracts/plugin_api.py`
- Предыдущие PR: #8 (`feature/langgraph-subgraph`), #9 (`feature/llm-disambiguate`), #10 (`feature/intent-eval`)
