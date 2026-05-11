# Модуль: Web Search

Веб-поиск через Brave Search API с определением языка запроса (RU → RU-источники, EN → EN) и фильтрацией по уровням источников: первый уровень — Reuters / Bloomberg / FT / OilPrice / Argus и т. п.; второй уровень — отраслевые блоги; третий уровень отбрасывается.

## Точка входа

- `nefteboros/search/__init__.py:49` — `WebSearcher.search(query, lang=auto, …) -> list[WebResult]`.
- Точка входа на уровне навыка: `skills/neftegaz_analyst/plugin.py:418` — `@traced_tool(name="web_search")`.

## Поток

1. `lang.detect_language(query)` — `auto` / `ru` / `en`.
2. `brave.search(query, lang)` — REST-вызов Brave API.
3. `tiers.classify_tier(domain)` — определение уровня домена по поддомену (фикс `ADR-0022` + последующие правки 2026-05-07: порядок классификации уровней, сопоставление поддомена, второй проход аудита).
4. Отбрасывание третьего уровня, формат `[Источник: <domain>, web]` для цитирования.

## Входы / выходы

**Вход:** `query: str`, опционально `lang_hint: "ru"|"en"|"auto"`, `count: int`.

**Выход:** `list[WebResult]` — `title`, `url`, `snippet`, `domain`, `tier`, `lang`. Потребляется `synthesize` (узел графа) или возвращается агенту напрямую как результат инструмента.

## Ключевые ADR

- [ADR-0022](../adr/0022-web-search-brave.md) — выбор Brave, классификация уровней, определение языка.

## Метрики

**Инструментация есть:**

- `brave_api_call` — retriever span (`nefteboros/search/brave.py:74`).
- Корневой span инструмента `web_search` — `skills/neftegaz_analyst/plugin.py:418`.

**Eval:** **выделенного eval-скрипта НЕТ.** Качество веб-поиска косвенно покрывается через:
- `eval_e2e.py` — citation_correct (включая веб-цитаты) и success rate на диалогах, где требуется веб (`spot`, `news`).
- Фильтр уровней покрыт unit-тестами (см. changelog'и `2026-05-07-web-tier-*`).

Сигнал координатору: **отдельный eval веб-поиска — gap.** Если веб критически важен (а он критически важен для spot-цен и свежих новостей), нужен retrieval-eval с эталонным датасетом «запрос → ожидаемые домены / факты».

## Известные ограничения

- Brave API: rate-limit и латентность могут вытянуть end-to-end время.
- На запросах про российский внутренний газовый рынок Brave часто возвращает шум — фильтр третьего уровня его снимает, но и второй уровень не всегда релевантен. См. `russian_gas_refusal` в [routing.md](routing.md).
- Анти-галлюцинационный валидатор цитат для веб-результатов на 2026-05-11 в бэклоге (валидируется только RAG, см. [citation.md](citation.md)).
