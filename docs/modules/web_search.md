# Модуль: Web Search

Веб-поиск через Brave Search API c определением языка запроса (RU → RU-источники, EN → EN) и tier-фильтрацией (tier-1 — Reuters/Bloomberg/FT/OilPrice/Argus/etc.; tier-2 — отраслевые блоги; tier-3 — отброшены).

## Точка входа

- `nefteboros/search/__init__.py:49` — `WebSearcher.search(query, lang=auto, …) -> list[WebResult]`.
- Skill-level tool entry: `skills/neftegaz_analyst/plugin.py:418` — `@traced_tool(name="web_search")`.

## Поток

1. `lang.detect_language(query)` — `auto` / `ru` / `en`.
2. `brave.search(query, lang)` — REST вызов Brave API.
3. `tiers.classify_tier(domain)` — определение tier по subdomain (фикс `ADR-0022` + post-fixes 2026-05-07: tier classify order, subdomain match, audit pass 2).
4. Отброс tier-3, формат `[Источник: <domain>, web]` для цитирования.

## Входы / выходы

**Вход:** `query: str`, optional `lang_hint: "ru"|"en"|"auto"`, `count: int`.

**Выход:** `list[WebResult]` — `title`, `url`, `snippet`, `domain`, `tier`, `lang`. Потребляется `synthesize` (узел графа) или возвращается агенту напрямую как tool result.

## Ключевые ADR

- [ADR-0022](../adr/0022-web-search-brave.md) — выбор Brave, tier классификация, language detection.

## Метрики

**Есть инструментация:**

- `brave_api_call` — retriever span (`nefteboros/search/brave.py:74`).
- Корневой tool span `web_search` — `skills/neftegaz_analyst/plugin.py:418`.

**Eval:** **выделенного eval-скрипта НЕТ.** Качество web search косвенно покрывается через:
- `eval_e2e.py` — citation_correct (включая web cite) и success rate на дилогах, где требуется web (`spot`, `news`).
- Tier-фильтрация покрыта unit-тестами (см. changelog'и `2026-05-07-web-tier-*`).

Сигнал для координатора: **отдельный eval web search — gap.** Если веб-поиск критически важен (а он критически важен для spot-цен и свежих новостей), нужен retrieval eval с эталонным датасетом «query → ожидаемые домены / факты».

## Известные ограничения

- Brave API: rate-limit / latency может задрать end-to-end время.
- На запросах про российский внутренний газовый рынок Brave часто возвращает шум — фильтр tier-3 это снимает, но и tier-2 не всегда релевантен. См. `russian_gas_refusal` в [routing.md](routing.md).
- Anti-hallucination валидатор цитат для web результатов на 2026-05-11 в backlog (валидируется только RAG, см. [citation.md](citation.md)).
