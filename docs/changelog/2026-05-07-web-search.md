# 2026-05-07 — Web search через Brave API + удаление upstream `web_search`

**PR:** `feature/web-search-integration`
**ADR:** [ADR-0022](../adr/0022-web-search-brave.md)

## Задача

Реализовать веб-поиск для агента `neftegaz_analyst` — закрыть требования ТЗ §2.3 (интернет-поиск с фильтрацией жёлтой прессы) и §2.4 (приоритизация RAG → web → forecast). До этого PR `nefteboros/search/__init__.py` был пуст, а в `prompts/SYSTEM.md` стоял TODO «web_search не зарегистрирован».

## Решение

Один PR в двух связанных шагах:

1. **Удалили upstream `web_search`** (OpenAI Responses API). Без `OPENAI_API_KEY` он возвращал explicit error, но шумел в tool selection LLM. Очищены `ouroboros/tools/search.py`, регистрации в `registry.py`/`safety.py`/`tool_capabilities.py`, тесты, упоминания в `test_smoke.py`/`test_contracts.py`/`test_chat_logs_ui.py`.

2. **Зарегистрировали свой `web_search`** как третий tool в skill `neftegaz_analyst` (multi-tool архитектура из ADR-0018). Доменный модуль `nefteboros/search/`:
   - `lang.py` — детектор языка по кириллице → `search_lang/country/ui_lang` для Brave; RU-запрос ловит RU-tier1 (Vedomosti/Kommersant/РБК), EN — EN-tier1 (Reuters/Bloomberg/FT).
   - `tiers.py` — TIER1/TIER2/BLACKLIST hostsets с ENV-override (`NEFTEBOROS_WEB_TIER1_HOSTS=...`).
   - `cache.py` — самодельный TTL-кэш (1ч) без cachetools.
   - `brave.py` — sync `BraveClient` через httpx, 1 retry на 429/5xx + exponential backoff, timeout 10 сек.
   - `__init__.py` — `WebSearcher` фасад: lang detect → cache → Brave → blacklist + tier filter → top-k.

## Изменения

### Удалено (upstream)

- `ouroboros/tools/search.py` — целиком.
- `tests/test_search_tool.py`, `tests/test_web_search_streaming.py` — целиком.
- Упоминания `web_search` в:
  - `ouroboros/tools/registry.py` — `CORE_TOOL_NAMES`, `_FROZEN_TOOL_MODULES`.
  - `ouroboros/safety.py` — `TOOL_POLICY`.
  - `ouroboros/tool_capabilities.py` — capability list, `READ_ONLY_PARALLEL_TOOLS`.
  - `tests/test_smoke.py` — `EXPECTED_TOOLS`.
  - `tests/test_contracts.py` — пример `requires` в манифесте (заменено на `chat_history`).
  - `tests/test_chat_logs_ui.py` — комментарий (заменено на `code_search`).

### Добавлено (доменный модуль)

- [`nefteboros/search/lang.py`](../../nefteboros/search/lang.py) — `detect_lang`, `brave_params_for_lang`.
- [`nefteboros/search/models.py`](../../nefteboros/search/models.py) — `SearchHit` dataclass.
- [`nefteboros/search/tiers.py`](../../nefteboros/search/tiers.py) — `classify`, `is_blacklisted`, `get_tier1/2`, `get_blacklist`, `normalize_hostname`.
- [`nefteboros/search/cache.py`](../../nefteboros/search/cache.py) — `TTLCache`.
- [`nefteboros/search/brave.py`](../../nefteboros/search/brave.py) — `BraveClient`, `BraveError/AuthError/RateLimitError`.
- [`nefteboros/search/__init__.py`](../../nefteboros/search/__init__.py) — `WebSearcher` (заменил placeholder).

### Изменено

- [`skills/neftegaz_analyst/plugin.py`](../../skills/neftegaz_analyst/plugin.py) — третий tool `web_search` через `register_tool(...)`, lazy import `nefteboros.search`, schema `{query, freshness?, k?, tier?}`, error-resilience.
- [`prompts/SYSTEM.md`](../../prompts/SYSTEM.md) — обновлён список tools (3 tool'а вместо 2), таблица tool-selection с web-кейсами, маркировка `[Источник: <hostname>, web]`, anti-hallucination правило для web-цитат.

### Тесты

Новые:
- `tests/test_search_lang.py` — детектор языка (9+ кейсов RU/EN/смешанные).
- `tests/test_search_tiers.py` — host classification + ENV-override (15+ кейсов).
- `tests/test_search_cache.py` — TTL behaviour (set/get/expire/evict).
- `tests/test_search_brave.py` — `BraveClient` через httpx-моки (auth/happy/lang/retry/error paths, 14 кейсов).
- `tests/test_search_websearcher.py` — фасад: blacklist filter, tier filter, k limit, кэш, error propagation (10 кейсов).
- `tests/test_neftegaz_skill_web_search.py` — smoke handler (input validation, no key, happy, error, truncation).

Обновлены:
- `tests/test_neftegaz_skill_smoke.py` — `test_register_two_tools_and_route` → `test_register_three_tools_and_route` с проверкой `web_search` schema.

## Конфигурация

ENV переменные (см. [.env.example](../../.env.example)):
- `BRAVE_API_KEY` — обязательно. Получить на <https://brave.com/search/api/>.
- `BRAVE_RESULTS_PER_QUERY` — read by `WebSearcher` (через ENV override на freshness не влияет).
- `BRAVE_FRESHNESS=pw` — дефолт окна свежести (`pd/pw/pm/py`).
- `NEFTEBOROS_WEB_TIER1_HOSTS=h1.com,h2.com` — переопределение TIER1 (полностью замещает default).
- `NEFTEBOROS_WEB_TIER2_HOSTS=...` — то же для TIER2.
- `NEFTEBOROS_WEB_BLACKLIST_HOSTS=...` — то же для BLACKLIST.

## Deployment notes

1. Поставить `BRAVE_API_KEY` в `.env` на Timeweb VDS (без него tool вернёт error при первом вызове).
2. После deploy — `curl https://<host>/api/extensions/neftegaz_analyst/health` → ожидаем `tools: ["analyst_query", "rag_search", "web_search"]`.
3. Smoke-вопрос в чате: «Что заявил Новак сегодня про OPEC+?» → ожидаем результаты с `[Источник: <hostname>, web]`.

## Метрики и оценка

E2E-eval для web не делаем — ground truth нестабилен (через сутки результаты меняются), количественная оценка не входит в ТЗ §2.3. Покрытие — smoke + 5 golden-сценариев из ТЗ §4.6 (проверяются вручную).

Unit-coverage по доменному модулю — все ветки фильтрации, lang detection, retry-логика BraveClient, error paths handler'а.

## Ограничения и follow-up

- Anti-hallucination validator на формат `[Источник: <hostname>, web]` — отдельный PR `feature/web-citations-validator`. Митигировано в SYSTEM.md явным правилом.
- Brave free tier 2000/мес — на демо хватит, для прод-нагрузки нужен платный план.
- Кэш in-memory на 1ч — теряется при restart. Disk/Redis cache — отдельная задача.
- LLM-translate узел не реализован (лежит в backlog `feature/web-llm-translate` на случай регресса на golden-сценариях).
