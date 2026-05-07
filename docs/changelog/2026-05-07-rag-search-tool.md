# Changelog: rag-search-tool — второй tool в neftegaz_analyst

- **Дата:** 2026-05-07
- **PR:** `feature/rag-search-tool`
- **ADR:** [docs/adr/0018-rag-search-tool.md](../adr/0018-rag-search-tool.md)
- **Связанные:** ADR-0016 (forecast-skill), ADR-0016-embed-retrieve, [rag-full-eval-report.md](../experiments/rag-full-eval-report.md)

## Задача

Сделать RAG retriever доступным агенту в Ouroboros loop'е. До этого PR `nefteboros.rag.retriever.Retriever` существовал как production-ready Python-API с полным эвалом (chunk_hit@5 = 0.779), но **не был зарегистрирован как tool** — агент не мог его вызвать.

## Решение (см. ADR-0018)

Добавили **второй tool `rag_search`** в существующий skill `skills/neftegaz_analyst/` через `api.register_tool(...)`. Skill стал мульти-tool. **Не создавали отдельный skill** — PluginAPI v1 поддерживает несколько tools, нет смысла плодить permissions / review-pipeline.

**Архитектура (см. ADR-0018):** агент-style routing вместо graph-only. Агент видит 2 tools, сам решает что вызвать (или оба для combined ответа из ТЗ §4.6). Это согласуется с ТЗ §2.4 (приоритизация — agent decision) и §2.5 (агент сам выбирает forecast).

## Что сделано

### Код

`skills/neftegaz_analyst/plugin.py`:
- Новые константы: `_RAG_TOOL_DESCRIPTION`, `_RAG_TOOL_SCHEMA`, `_RAG_DEFAULT_K=5`, `_RAG_MAX_K=10`, `_RAG_MAX_TEXT_CHARS=4000`
- `_serialize_rag_hit(hit)` — RankedHit → JSON dict с метаданными для citation
- `_tool_rag_search(query, k)` — main handler:
  - Валидация input (empty / too long / k clamp)
  - Lazy import `Retriever` (chromadb + sentence-transformers + torch не вытаскиваются на skill load)
  - `Retriever().retrieve(query, k_dense=max(30, k×6), k_final=k)` — production default
  - Defensive — все исключения → `{"error": ...}` JSON, не raise
  - Truncate text per chunk до 4000 chars (защита tool response от 40K-токеновых ответов на максимуме k)
- `register(api)` — добавлен второй `register_tool` вызов с `timeout_sec=30`
- `_route_health` — обновлён, возвращает оба tool'а в payload

`skills/neftegaz_analyst/SKILL.md`:
- Frontmatter — version 0.1.0 → 0.2.0, обновлён `description` и `when_to_use`
- Введение переписано — два tools, multi-tool архитектура, ссылка на ADR-0018
- Таблица `Зарегистрированные surfaces` — добавлена строка для `rag_search`
- Permissions — `register_tool × 2`
- Новая секция «Когда какой tool вызывать» с примерами запросов и ожидаемым выбором
- Архитектурная диаграмма — обновлена под два параллельных tool path

### Документация

- `docs/adr/0018-rag-search-tool.md` — детальное обоснование multi-tool design'а, аргументы против single-tool stance ADR-0016, альтернативы, последствия
- `docs/changelog/2026-05-07-rag-search-tool.md` — этот файл

## Что НЕ в PR

- **Системный промпт** для агента с приоритизацией RAG → web → forecast → отдельный PR `feature/system-prompt-analyst`. Без него default `rag_search` не будет автоматически выбираться (агент в default режиме «I am self-modifying AI» предпочитает agentic chitchat). Нужен manual hint в первом сообщении или skill enable + system prompt rewrite.
- **`web_search` tool** → отдельный PR `feature/web-search-integration` после Brave/Tavily интеграции
- **UI tab** → `feature/analyst-ui-widget`
- **Combined synthesis (RAG + analyst_query)** в одном tool — НЕ нужно: combined ответ происходит естественно когда агент вызывает оба tool'а
- **Topic-filter режимы как default** — остаются опциональными (env `NEFTEBOROS_TOPIC_FILTER=...`), на synthetic dataset регрессы (см. ADR-0016-embed-retrieve)
- **`feature/forecast-with-rag-context`** — overlay RAG-контекста в analyst_graph (для запросов типа «Brent 3m с учётом OPEC решений») → отдельный PR в backlog

## Файлы

**Изменено:**
- `skills/neftegaz_analyst/plugin.py` — добавлены ~100 строк (rag_search tool spec + handler + register)
- `skills/neftegaz_analyst/SKILL.md` — переписана manifest section + структурные изменения

**Добавлено:**
- `docs/adr/0018-rag-search-tool.md`
- `docs/changelog/2026-05-07-rag-search-tool.md`
- `tests/test_neftegaz_skill_smoke.py` — расширен, добавлены тесты для rag_search

## Тесты

- AST OK для `plugin.py`
- Smoke без vectorstore: `_tool_rag_search(query="")` → `{"error": "query is empty"}` ✓
- Smoke без vectorstore: `_tool_rag_search(query="x"*3000)` → `{"error": "query too long"}` ✓
- Smoke с vectorstore (env `NEFTEBOROS_RAG_VECTORSTORE_PATH=...`): `_tool_rag_search(query="OPEC квоты", k=3)` → 3 chunks, корректные source_id (opec_annual_report_2024 / opec_asb_2024 / eia_steo) ✓
- pytest unit-tests в `tests/test_neftegaz_skill_smoke.py` — проверка register выполняется (capture-mock api), tool возвращает корректный JSON shape

## Deployment notes

После merge для production-deploy:
1. На сервере должен быть `data/vectorstore/` (~65 МБ, gitignored). Перенести через `scp` с dev-машины или собрать через `python scripts/build_index.py` (требует CPU 2-3 ч или GPU 12 мин).
2. Проверить healthcheck: `curl http://server:port/api/extensions/neftegaz_analyst/health` — должен вернуть `tools: ["analyst_query", "rag_search"]`.
3. После Ouroboros restart первый вызов `rag_search` — 7-10 сек cold-start (BGE-M3 model load, ~2.3 ГБ из ~/.cache/huggingface/). Последующие — <1 сек.
4. Без системного промпта (`feature/system-prompt-analyst`, отдельный PR) — агент **не будет автоматически** выбирать `rag_search`. Нужно либо явно сказать «используй rag_search для X» в первом сообщении, либо подождать systemprompt-PR.
