# 2026-05-04 — Init skeleton (feature/init-skeleton)

## Задача

Создать скелет проекта `nefteboros`: форк Ouroboros, базовая структура каталогов, документация процесса (ТЗ, ADR, architecture), placeholder'ы для всех будущих модулей.

## Контекст

Проект — тестовое задание Сбера на разработку AI-агента «нефтегазовый аналитик» с обязательным использованием Ouroboros. Дедлайн 2026-05-12 12:00 МСК. Архитектурное решение — fork Ouroboros (см. [ADR-0001](../adr/0001-fork-ouroboros.md)).

## Что сделано

### Репозиторий
- Создан публичный репозиторий `ArtemSamoilov/nefteboros` как fork от `joi-lab/ouroboros-desktop` (MIT)
- Ветка `feature/init-skeleton` от `main`

### Документация
- `docs/tz/original.md` — ТЗ дословно
- `docs/adr/0001-fork-ouroboros.md` — обоснование выбора варианта B (fork)
- `docs/architecture.md` — целевая архитектура с Mermaid-диаграммами (high-level, sequence, компонентная)
- `docs/changelog/` — этот документ; шаблон для последующих PR
- `docs/experiments/design.md` — дизайн экспериментов и метрики (placeholder)

### Структура `nefteboros/` (новый код)
Созданы пакеты-заглушки:
- `nefteboros/rag/` — RAG-пайплайн (PDF → чанки → BGE-M3 → ChromaDB)
- `nefteboros/forecast/` — ARIMA + Prophet прогноз цен Brent
- `nefteboros/search/` — Brave API с tier-1/tier-2 фильтром
- `nefteboros/llm/` — GigaChat и Cloud.ru-OpenAI-compatible адаптеры
- `nefteboros/graphs/` — LangGraph subgraphs (analyst_graph)
- `nefteboros/prompts/` — системный промпт аналитика
- `nefteboros/citations/` — пост-валидатор источников
- `nefteboros/bot/` — Telegram-бот на aiogram

### Skill для Ouroboros
- `skills/neftegaz_analyst/SKILL.md` — манифест (placeholder)
- `skills/neftegaz_analyst/plugin.py` — точка входа PluginAPI (placeholder)

### Данные и метрики
- `data/corpus/` — папка для PDF (gitignored, кроме .gitkeep)
- `data/metadata/` — yaml-метаданные отчётов
- `data/vectorstore/` — Chroma persist (gitignored)
- `datasets/` — эталонные датасеты для оценки (rag_qa.jsonl, routing.jsonl, citations_gold.jsonl, forecast_history.csv, e2e_dialogues.jsonl)
- `metrics/runs/` — результаты прогонов eval-скриптов
- `scripts/eval/` — заглушки для eval_rag.py, eval_routing.py, eval_citations.py, eval_forecast.py, eval_llm.py, eval_e2e.py, make_dashboard.py

### Конфигурация
- `.env.example` — список всех ключей (GigaChat, Cloud.ru, Brave, Telegram, model selection)
- `requirements-domain.txt` — наш доменный стек (langchain, langgraph, chromadb, prophet, statsmodels, pymupdf, sentence-transformers, aiogram, gigachat)
- `NOTICE` — уведомление о форке Ouroboros (MIT)
- Обновлён `.gitignore`: добавлены `.claude/`, `data/corpus/*` (кроме `.gitkeep`), `data/vectorstore/*`, `metrics/runs/*`, `.embeddings_cache/`

### Деплой
- `deploy/Dockerfile.app` — заготовка multi-stage сборки
- `deploy/docker-compose.yml` — заготовка для web + bot + chroma сервисов
- `deploy/timeweb-setup.md` — инструкция деплоя (placeholder)

### README
- Заменён корневой `README.md` на наш (описание `nefteboros`, инструкция запуска — placeholder)
- Оригинальный README Ouroboros сохранён в `docs/upstream-ouroboros-readme.md`

## Файлы

**Добавлены:**
- `docs/tz/original.md`, `docs/adr/0001-fork-ouroboros.md`, `docs/architecture.md`
- `docs/changelog/2026-05-04-init-skeleton.md`, `docs/experiments/design.md`
- `nefteboros/{rag,forecast,search,llm,graphs,prompts,citations,bot}/__init__.py`
- `skills/neftegaz_analyst/{SKILL.md,plugin.py}`
- `scripts/eval/__init__.py` + 7 заглушек `eval_*.py`
- `data/{corpus,metadata,vectorstore}/.gitkeep`
- `datasets/.gitkeep`, `metrics/runs/.gitkeep`
- `.env.example`, `requirements-domain.txt`, `NOTICE`
- `deploy/{Dockerfile.app,docker-compose.yml,timeweb-setup.md}`

**Изменены:**
- `.gitignore` — добавлены наши паттерны
- `README.md` — наш проектный README (оригинал в `docs/upstream-ouroboros-readme.md`)

**Удалено:** ничего (выпил self-modify подсистем — отдельный PR `feature/rip-self-modify`)

## Тесты

В этом PR — нет (структурный скелет). Существующие тесты Ouroboros не трогаем.

## Известные ограничения / TODO для следующих PR

- [ ] `feature/rip-self-modify` — выпил consciousness/reflection/deep_self_review/improvement_backlog/consolidator + правка `server.py`
- [ ] `feature/llm-providers` — реальные адаптеры GigaChat и Cloud.ru
- [ ] `feature/rag-pipeline` — парсер PDF + индексатор + retriever
- [ ] `feature/web-search` — Brave API + фильтр
- [ ] `feature/forecast` — ARIMA + Prophet
- [ ] `feature/langgraph-subgraph` — analyst_graph.py с маршрутизацией
- [ ] `feature/citations-validator` — anti-hallucination валидатор
- [ ] `feature/skill-integration` — реальный SKILL.md + plugin.py
- [ ] `feature/telegram-bot` — aiogram-бот с auth
- [ ] `feature/eval-*` — реализация eval-скриптов
- [ ] `feature/corpus-loader` — скрипт загрузки 10 PDF + metadata.yaml
- [ ] `feature/docker-compose` — рабочий docker-compose с web+bot+chroma
- [ ] `feature/demo-scenarios` — 5+ демо-диалогов

## Связанные документы

- ТЗ: [docs/tz/original.md](../tz/original.md)
- ADR-0001: [docs/adr/0001-fork-ouroboros.md](../adr/0001-fork-ouroboros.md)
- Architecture: [docs/architecture.md](../architecture.md)
