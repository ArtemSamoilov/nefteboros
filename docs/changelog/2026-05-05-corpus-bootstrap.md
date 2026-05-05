# Changelog: corpus-bootstrap

- **Дата:** 2026-05-05
- **PR:** `feature/corpus-bootstrap`
- **ADR:** [docs/adr/0009-corpus-strategy.md](../adr/0009-corpus-strategy.md)

## Задача

Собрать первоначальный RAG-корпус нефтегазового аналитика — стратегические документы, корпоративная отчётность РФ-мейджоров, свежий operational срез глобального рынка, тематические обзоры по геополитике/санкциям.

## Контекст

Дискуссия с Артёмом (см. сессию):
- **TG-канал отменили** в пользу Ouroboros web UI как единственного канала (записано в auto-memory project).
- **Корпус строим под taxonomy вопросов**, не «как можно больше документов»: 8 категорий (RU операционка / госфинансы / санкции / краткосрочные цены / долгосрок / глобальные балансы / газ / корпоративка) → отбор источников.
- **Фокус — РФ-периметр**, баланс RU/EN ~50/50 (Артём настоял отойти от моего изначального 70/30).
- **Глобальные мейджоры исключены** (Aramco/Shell/Exxon) — «полезность, а не перегруженность». Глобальный взгляд через OPEC WOO/AR + IEA Oil/Gas + GIIGNL.
- **Архив monthly-документов глубже 1 номера не берём** — за свежие новости отвечает web-search в runtime.

## Что сделано

### Код

- `scripts/fetch_corpus.py` — идемпотентный CLI fetcher по манифесту. Поддерживает `--check` (только сверить sha256), `--only <substr>` (фильтр), `--force` (перекачать). Предупреждает о sha mismatch не молча. Pure stdlib + PyYAML.
- `scripts/fetch_corpus_manual.sh` — bash-помощник для документов, недоступных через основной fetcher (российские сайты с региональным/WAF блоком — Газпром AR в первую очередь). Запускается с VPN РФ.

### Данные

- `data/metadata/manifest.yml` — манифест из **25 документов**, 4 блока, со всеми полями (id/title/publisher/language/type/date/url/file/size_bytes/sha256/tags/purpose). Плюс блоки `known_limitations` и `excluded_by_design`.
- `data/corpus/` — 25 PDF скачаны (~135 МБ): 23 автоматически через `fetch_corpus.py`, 2 руками через VPN/регистрацию (Газпром AR 2024, OPEC WOO 2025). Каталог **gitignored** (только `.gitkeep` в репо).

### Документация

- `docs/corpus.md` — **человекочитаемый каталог корпуса**: разделение по 4 блокам, 2-3 предложения описания на каждый документ (что внутри, для каких вопросов используется), раздел «Обоснование достаточности набора» с taxonomy вопросов, покрытием 5 демо-сценариев ТЗ, балансом RU/EN и нефть/газ.
- `docs/adr/0009-corpus-strategy.md` — ADR с обоснованием архитектурных решений, lifecycle, известных ограничений и сознательных исключений.
- `docs/changelog/2026-05-05-corpus-bootstrap.md` — этот файл.

## Что НЕ в этом PR

- **RAG-пайплайн** (chunking, BGE-M3, Chroma, retrieval, re-ranking) → следующий PR `feature/rag-pipeline` + ADR-0010.
- **Web-search tool** runtime → PR `feature/web-search`.
- **Ценовые tools** (Brent/WTI/Urals/Henry Hub/TTF API) → PR `feature/price-tools`.
- **Vygon Consulting** — manual для v1 (страница защищена WAF от curl), отложено как опциональное.
- **Eval RAG retrieval-метрик** → после `feature/rag-pipeline`, в `feature/eval-rag`.

## Файлы

**Добавлено:**
- `data/metadata/manifest.yml` (25 docs)
- `scripts/fetch_corpus.py`
- `scripts/fetch_corpus_manual.sh`
- `docs/corpus.md` (каталог + обоснование достаточности)
- `docs/adr/0009-corpus-strategy.md`
- `docs/changelog/2026-05-05-corpus-bootstrap.md`

**Изменено:** —

**Удалено:** —

## Тесты

- `python3 -c "import ast; ast.parse(open('scripts/fetch_corpus.py').read())"` — AST OK
- `python3 -c "import yaml; yaml.safe_load(open('data/metadata/manifest.yml').read())"` — YAML OK
- `python3 scripts/fetch_corpus.py --check` — все 23 скачанных файла прошли sha256 check, 2 в статусе `manual` (ожидаемо)

## Известные ограничения (cм. ADR-0009)

- IEA OMR full — paywall, используется free-version
- OPEC WOO — требует одноразовой регистрации; скачан вручную как WOO 2025. OPEC AR 2024 + ASB 2024 остаются как комплементарные источники.
- OPEC MOMR — Cloudflare, заменён на IEF Comparative Analysis (даже лучше — три источника в одном)
- CSIS Iran — только HTML, заменены на CRS PDF reports
- Vygon — WAF блок curl, опционален вручную
- Газпром МСФО — не публикуется с 2022, есть только Бухгалтерская РСБУ-отчётность
- Газпром AR 2024 — собран вручную через VPN РФ (Артём)
- OPEC WOO 2025 — скачан вручную после регистрации на publications.opec.org (Артём)
