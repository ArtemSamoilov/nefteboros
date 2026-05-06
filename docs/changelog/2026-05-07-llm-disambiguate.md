# Changelog: feature/llm-disambiguate — GigaChat-узел для no_keyword_match

- **Дата:** 2026-05-07
- **PR:** `feature/llm-disambiguate` (расширение analyst graph через ADR-0015)
- **ADRs:** [0015 — LLM-disambiguate узел через GigaChat-2-Max](../adr/0015-llm-disambiguate.md)

## Задача

Закрыть deficit rule-based `classify_intent`: запросы с формулировкой вне keyword-набора (например, «прогноз чёрного золота для российского ТЭК», «Bonny Light на квартал», «энергоносители для казны») сейчас falling through в `out_of_scope` с `matched_rule="no_keyword_match"`. Артём (assignee аналитика для Сбера): «в rule-based regex я не верю; LLM с хорошим промптом решает эту задачу намного лучше». Заодно — карьерная ценность демонстрации **GigaChat в реальной задаче проекта-в-Сбер**.

## Контекст

PR `feature/langgraph-subgraph` (#8) реализовал rule-based classify_intent с 53 unit-тестами. Известное ограничение — формулировки вне keyword-набора. После обсуждения выбрана **hybrid disambiguation**: rule-based как fast path (fast/deterministic для типовых запросов), LLM-узел `llm_disambiguate` через GigaChat-2-Max — fallback **только** на `no_keyword_match`. Refusal'ы `rule_5_russian_gas` и `rule_3_horizon` остаются deterministic — не идут в LLM (refusal-text уже составлен под конкретное правило, экономим токены).

## Что сделано

### ADR

- `docs/adr/0015-llm-disambiguate.md` — обоснование hybrid (LLM только на no_keyword_match), почему через существующий `nefteboros.llm.gigachat` (langchain-gigachat), почему GigaChat-2-Max, почему отдельный узел а не fold в classify, fallback-стратегия.

### Узел `llm_disambiguate`

- `nefteboros/graphs/nodes/llm_disambiguate.py`:
  - Lazy import `nefteboros.llm.gigachat.get_gigachat_chat_model` (langchain-gigachat).
  - `_LLMIntent` (pydantic) — структура structured output. Конвертируется в `Intent` с `matched_rule="llm_<type>"`.
  - Сценарий: `with_structured_output(_LLMIntent)` → если NotImplementedError/AttributeError → fallback на raw chat.ainvoke + JSON parse.
  - Resilient-fallback'и:
    - `ImportError` → `matched_rule="llm_unavailable_import"`
    - `ValueError` (GIGACHAT_CREDENTIALS не задан) → `matched_rule="llm_unavailable_creds"`
    - `JSONDecodeError`/`ValidationError` → `matched_rule="llm_parse_failed"`
    - Прочие Exception → `matched_rule="llm_error_<TypeName>"`
  - Промпт читается из `nefteboros/prompts/disambiguate_intent.md`. `{ASSET_LIST}` генерируется runtime из `ASSET_REGISTRY` (single source of truth).

### Промпт

- `nefteboros/prompts/disambiguate_intent.md`:
  - 4 типа intent с описанием.
  - 5 правил ADR-0013 §Constraints (короткой формулировкой).
  - Список валидных активов (placeholder `{ASSET_LIST}`).
  - 5 few-shot examples: forecast_with_context (РФ-контекст), forecast_simple (Bonny Light → brent proxy), russian_gas_refusal, out_of_scope, forecast_with_context (нефтегаздоходы).
  - Strict ответ — только JSON без markdown.

### Wiring

- `nefteboros/graphs/analyst_graph.py`:
  - Добавлен узел `llm_disambiguate`.
  - Новая predicate `_route_after_classify_initial` после classify:
    - `no_keyword_match` → `llm_disambiguate`.
    - refusal'ы (rule #5 / #3) → `synthesize` (без LLM, deterministic).
    - forecast intents → `forecast_call`.
  - Существующая predicate `_route_after_classify` переиспользована после `llm_disambiguate` — стандартный routing forecast/synthesize.
  - Граф diagram обновлена в module docstring'е.

- `nefteboros/graphs/nodes/__init__.py` — экспорт `llm_disambiguate`.

### Тесты

- `tests/test_llm_disambiguate.py` — **7 unit-тестов**:
  - structured_output happy path (forecast_simple, forecast_with_context).
  - raw fallback (no structured) с JSON-parse → forecast_simple (Bonny Light proxy).
  - invalid JSON → matched_rule="llm_parse_failed".
  - no creds → matched_rule="llm_unavailable_creds".
  - API error (ConnectionError) → matched_rule="llm_error_ConnectionError".
  - invalid horizon → forecast_horizon=None, остальные поля корректные.
  - Все mock'и через `monkeypatch.setattr` на `nefteboros.llm.gigachat.get_gigachat_chat_model`.

- `tests/test_graph_smoke.py` — **2 новых теста**:
  - `test_smoke_llm_disambiguate_routes_to_forecast` — «прогноз чёрного золота на квартал» через LLM → forecast_simple → forecast_call → synthesize.
  - `test_smoke_llm_disambiguate_unavailable_falls_back_to_synthesize` — GigaChat creds не заданы → fallback → out_of_scope → synthesize без LLM.
  - Существующий `test_smoke_out_of_scope_skips_forecast_and_synthesize_llm` обновлён: теперь явно мокает GigaChat возвращающий out_of_scope (после добавления llm-узла rule-based out_of_scope с no_keyword_match идёт в LLM, не bypass'ит).

**Итого: 68/68 passed** (53 intent_classifier + 8 graph_smoke + 7 llm_disambiguate).

### Зависимости

`langchain-gigachat>=0.3.0` уже в `requirements-domain.txt:12`. В локальный venv (для тестов) установлены `langchain-gigachat 0.5.1` и `pytest-asyncio 1.3.0` (но тесты используют `asyncio.run`, не зависят от pytest-asyncio mode). Изменений в `requirements-*.txt` нет.

## Что НЕ в этом PR (явно)

- **`OUROBOROS_LLM_DISAMBIGUATE_ENABLED`-флаг для отключения** — graceful fallback на error эквивалентен.
- **Function-call API** (вместо JSON-mode response_format) — текущий путь через `with_structured_output` достаточен; если в production упадёт стабильность parsing — переключим.
- **Real-LLM golden-eval датасет** — фиксированный набор формулировок для нерегулярных запросов. Отдельный PR.
- **Метрики latency / token usage** в state.metadata — узел сейчас перезаписывает intent. Метрики через `logger.info` (telemetry-grade observability отложена).
- **Multi-language disambiguate** — текущий промпт RU+EN, без отдельного language detection. Отдельная фича.
- **Расширение classify_intent rule'ами** под формулировки, которые часто видим в LLM-output — адаптация на real telemetry, отдельный PR.

## Тесты

- AST-парсинг прошёл по всем новым/изменённым `.py`.
- pytest 68/68 passed (53 intent_classifier + 8 graph_smoke + 7 llm_disambiguate).
- Real GigaChat не вызывается — все mock'и через `monkeypatch.setattr`. Real-LLM smoke остаётся manual / на сервере (где `GIGACHAT_*` env реально настроены).

## Файлы

**Добавлено (4 файла):**

- `docs/adr/0015-llm-disambiguate.md`
- `docs/changelog/2026-05-07-llm-disambiguate.md` (этот файл)
- `nefteboros/graphs/nodes/llm_disambiguate.py`
- `nefteboros/prompts/disambiguate_intent.md`
- `tests/test_llm_disambiguate.py`

**Изменено (3 файла):**

- `nefteboros/graphs/nodes/__init__.py` — экспорт `llm_disambiguate`.
- `nefteboros/graphs/analyst_graph.py` — новый узел + `_route_after_classify_initial` predicate + conditional edges.
- `tests/test_graph_smoke.py` — 2 новых теста + 1 обновлён под изменённый routing.

**Удалено:** —

## Связанные документы

- ADR-0015: [docs/adr/0015-llm-disambiguate.md](../adr/0015-llm-disambiguate.md)
- ADR-0014: [docs/adr/0014-langgraph-subgraph.md](../adr/0014-langgraph-subgraph.md) — minimal-graph baseline
- ADR-0013 §«Constraints for SKILL.md»: 5 правил disambiguation, теперь применяются и rule-based'ом, и LLM
- ADR-0007: [docs/adr/0007-llm-providers.md](../adr/0007-llm-providers.md) — выбор GigaChat через langchain-gigachat
- Reference паттерн: `app/classifier/gigachat_client.py` в anima_backend (token-cache, retry, profanity_check=False)
- Предыдущий PR: #8 `feature/langgraph-subgraph` — rule-based classify + minimal graph
