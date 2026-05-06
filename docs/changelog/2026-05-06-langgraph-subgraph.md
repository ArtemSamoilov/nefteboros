# Changelog: feature/langgraph-subgraph — analyst graph (PR2 после rescope)

- **Дата:** 2026-05-06
- **PR:** `feature/langgraph-subgraph` (PR2 — изначально планировался как `feature/forecast-skill`, перерешён в graph-first после саморазгрома)
- **ADRs:** [0014 — LangGraph analyst subgraph design](../adr/0014-langgraph-subgraph.md)

## Задача

Реализовать analyst graph как rule-based deterministic orchestrator между Ouroboros tool dispatch'ем и доменной логикой `forecast()`. Закрыть deficit правил disambiguation, обнаруженный при попытке сделать skill-first архитектуру.

## Контекст

Изначально PR2 планировался как `feature/forecast-skill` — Ouroboros skill `neftegaz_analyst` обёрткой над `forecast()`. Саморазгром (см. ADR-0014 §Контекст) выявил критические проблемы:

1. body SKILL.md в Ouroboros — review-pack для людей, не доходит до LLM-агента.
2. Правила disambiguation в `tool.description` — best-effort, не enforced.
3. Двойной tool surface (`oil_gas_forecast` + `analyst_query`) — двусмысленность для LLM.

Решение: graph-first. Правила disambiguation реализованы как rule-based код в LangGraph subgraph + unit-тесты + один tool surface (`analyst_query` в последующем тонком skill PR).

## Что сделано

### ADR

- `docs/adr/0014-langgraph-subgraph.md` — дизайн графа, обоснование graph-first vs skill-first, rule-based vs LLM-classify, conditional edges vs router узел, `ouroboros.llm` vs LangChain LLM.

### Состояние и intent

- `nefteboros/graphs/state.py` — pydantic схемы:
  - `IntentType`: forecast_simple / forecast_with_context / russian_gas_refusal / out_of_scope.
  - `Intent`: assets, horizon, refuse_reason, matched_rule (для debug/тестов).
  - `Citation`: forecast_model / forecast_metadata / rag_chunk / web_url.
  - `GraphState`: query, intent, forecast_results, forecast_errors, synthesis, citations, validation_warnings.

### Rule-based classify_intent

- `nefteboros/graphs/intents.py`:
  - `classify_intent(query) → Intent` — earliest-match-wins по правилам #1, #3, #5 из ADR-0013 §Constraints.
  - `extract_horizon(query) → (Horizon | None, refuse_reason | None)` — парсинг «квартал/полгода/год/N мес/N лет», 1d/1w → polite refuse, ≥18m → redirect к сценариям RAG.
  - Keyword-наборы с аккуратными word-boundary'ями: `газ\w{0,3}` ловит «газ/газа/газами», но не «Газпром».
  - Rules #2 (unknown asset proxy) и #4 (derived без method) — отложены в integration PR'ах.

### Узлы графа

- `nefteboros/graphs/nodes/forecast.py` — `forecast_call`. Lazy import `nefteboros.forecast.api.forecast` (heavy stack: pandas/numpy/statsmodels/sklearn/yfinance). Sequential по активам. Не падает на ошибках одного актива — собирает в `forecast_errors`, остальные прогнозируются.
- `nefteboros/graphs/nodes/synthesize.py` — `synthesize`. Refusal intent → `intent.refuse_reason` без LLM (экономия токенов). Forecast intent → `ouroboros.llm.LLMClient.chat_async` через шаблон `synthesize_forecast_only.md`. Graceful degradation на ImportError / LLM error.
- `nefteboros/graphs/nodes/validate.py` — `validate_citations`. Light pass: regex `[<...>]` ссылки в synthesis должны быть среди `state.citations`; hallucinated tags → `validation_warnings` (soft signal). Числовые диапазоны типа `[$70.00, $101.00]` фильтруются (не считаются цитатами).

### Prompts

- `nefteboros/prompts/system_analyst.md` — роль аналитика, обязательные disclaimers, явное упоминание current PR ограничений (RAG/web pending).
- `nefteboros/prompts/synthesize_forecast_only.md` — template с placeholder'ами `{{QUERY}}`, `{{INTENT}}`, `{{FORECAST_RESULTS_JSON}}`, `{{FORECAST_ERRORS}}`.

### Wiring

- `nefteboros/graphs/analyst_graph.py` — `build_analyst_graph()`. StateGraph с conditional edges:
  - russian_gas_refusal / out_of_scope → bypass `forecast_call`, в `synthesize`.
  - forecast_simple / forecast_with_context → `forecast_call` → `synthesize`.
  - `synthesize` → `validate_citations` → `END`.

### Тесты

- `tests/test_intent_classifier.py` — **53 теста**:
  - empty / whitespace → out_of_scope
  - rule #5 russian gas refusal: 5 формулировок
  - rule #3 horizon: 1d/1w/завтра/неделя + ≥18m
  - rule #1 generic: brent / wti / ttf / hh / generic gas
  - rule #1 РФ-контекст: 5 формулировок Минфин/бюджет/налог/нефтегаздоход
  - extract_horizon: квартал/полгода/год/N месяцев/округление 4→3
  - rule ordering: russian_gas > horizon > asset

- `tests/test_graph_smoke.py` — **6 smoke-тестов** е2е с monkeypatch'ами на `forecast()` и `LLMClient.chat_async`:
  - forecast_simple brent — полный путь classify → forecast → synthesize → validate.
  - russian_gas_refusal — forecast/LLM не вызываются.
  - out_of_scope — forecast/LLM не вызываются.
  - 24m horizon refusal — forecast пропускается.
  - forecast() RuntimeError — узел не падает, synthesize отрабатывает с пустым результатом.
  - РФ-контекст → 3 forecast() вызова (brent, urals, urals_minfin_blend).

**Итого: 59/59 passed.**

## Что НЕ в этом PR (явно)

- **`nefteboros/contracts/rag.py`** — Chunk schema. Будет в `feature/rag-integration` со своим контрактом, диктуемым реальной retrieval-логикой (YAGNI: не проектируем интерфейс системы, которой ещё нет).
- **`nodes/rag.py` / `nodes/web.py`** — узлы. В `feature/rag-integration` / `feature/web-integration` со своими pydantic-контрактами.
- **`synthesize_with_overlay`** — расширение `synthesize` под RAG/web overlay. Тот же PR, что и узлы.
- **`skills/neftegaz_analyst`** — Ouroboros wrapper-tool над graph. После integration — последующий тонкий skill PR (один tool `analyst_query`, ~50 LOC, обёртка над `analyst_graph.ainvoke`).
- **Правка system prompt'а Ouroboros форка** (роль «Старший аналитик») — отдельный PR `feature/system-prompt-analyst`.
- **Правило #2** (unknown asset → web-search → semantic family → proxy) — требует web-search, отложено.
- **Правило #4** (derived без method → fail loudly) — правка `nefteboros/forecast/api.py`, отдельный мелкий PR.
- **5 demo-сценариев** ТЗ §4.6 — отдельный PR `feature/demo-scenarios` после integration.
- **Markov-Switching / GARCH / Baumeister-Kilian** — PR3 (см. ADR-0012 §«Что НЕ в PR1»).

## Зависимости

`langgraph>=0.2.0` уже было в `requirements-domain.txt:8`. Установленная версия в локальном venv: langgraph 1.1.10, langchain-core 1.3.3 — обратно совместимы со ссылкой `>=0.2.0`. Изменений requirements в этом PR нет.

## Тесты

- AST-парсинг прошёл по всем новым `.py`.
- pytest 59/59 passed. Real LLM и real `forecast()` в smoke не вызываются — `monkeypatch.setattr` для `nefteboros.forecast.api.forecast` и `ouroboros.llm.LLMClient.chat_async`.
- pytest установлен в локальный venv (не был раньше — pyproject.toml содержит `[tool.pytest.ini_options]`, но сам пакет не listed как dev-dep).

## Файлы

**Добавлено (12 файлов):**

- `docs/adr/0014-langgraph-subgraph.md`
- `docs/changelog/2026-05-06-langgraph-subgraph.md` (этот файл)
- `nefteboros/graphs/state.py`
- `nefteboros/graphs/intents.py`
- `nefteboros/graphs/analyst_graph.py`
- `nefteboros/graphs/nodes/__init__.py`
- `nefteboros/graphs/nodes/forecast.py`
- `nefteboros/graphs/nodes/synthesize.py`
- `nefteboros/graphs/nodes/validate.py`
- `nefteboros/prompts/system_analyst.md`
- `nefteboros/prompts/synthesize_forecast_only.md`
- `tests/test_intent_classifier.py`
- `tests/test_graph_smoke.py`

**Изменено:** —

**Удалено:** —

## Связанные документы

- ADR-0014: [docs/adr/0014-langgraph-subgraph.md](../adr/0014-langgraph-subgraph.md)
- ADR-0013 §«Constraints for SKILL.md»: [docs/adr/0013-hybrid-forecasting.md](../adr/0013-hybrid-forecasting.md) — те же 5 правил, теперь реализованы как код
- ADR-0012: [docs/adr/0012-price-tools.md](../adr/0012-price-tools.md) — `forecast()` API
- Архитектура: [docs/architecture.md](../architecture.md) — место graph внутри `analyst_query` tool'а
- Предыдущий PR: feature/price-tools (#7) — расчётный модуль `forecast()`
