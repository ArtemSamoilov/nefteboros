# ADR-0001 — Fork of Ouroboros vs альтернативы

- **Дата:** 2026-05-04
- **Статус:** Принято
- **Контекст:** Скелет проекта `nefteboros`

## Контекст и проблема

ТЗ на «нефтегазового аналитика» содержит требование «использовать Ouroboros». Ouroboros (`joi-lab/ouroboros-desktop`, MIT) — это кросс-платформенное desktop-приложение самомодифицирующегося AI-агента: web UI, multi-provider LLM роутер (OpenAI/OpenRouter/Cloud.ru/OpenAI-compatible), tools system, skills через манифест + plugin, layered safety (sandbox + policy LLM check), Telegram bridge, локальные модели.

Способ использования Ouroboros допускает три интерпретации:

| Вариант | Суть |
|---|---|
| **A. Skill** | Минимальный SKILL.md + plugin.py как extension к стоковому Ouroboros |
| **B. Fork** | Форк репозитория, выпил неподходящих подсистем, добавление доменной логики |
| **C. Standalone** | Свой проект на LangChain, Ouroboros упоминается архитектурно |

## Решение

Выбран **вариант B (fork)**.

## Аргументация

**Почему не A (skill)** — расширение через PluginAPI v1 ограничено правами `[net, tool, route, widget]` и фиксированной структурой манифеста. Хранилище документов, индексация PDF, persist векторной базы выходят за рамки skill state_dir. Демо-сценарии требуют управляемого UI (Streamlit/наш ребрендинг) — skill-уровень не даёт нужного контроля. И, главное, skill оставляет на проверяющем установку Ouroboros отдельно — лишний шаг.

**Почему не C (standalone)** — формальное требование ТЗ «использовать Ouroboros». Ссылкой на архитектурное сходство в README не отделаться: проверяющий ожидает, что код реально стоит на Ouroboros.

**Почему B сработает за 8 дней** — анализ импортов показал, что сцепленность подсистем самомодификации (consciousness, reflection, deep_self_review, improvement_backlog, consolidator) — bolt-on, не пронизывание ядра:
- `loop.py` и `agent.py` НЕ импортируют consciousness/reflection. Это изолированный tool-loop.
- Использование `BackgroundConsciousness` — точечно в `server.py` (~30 ссылок, все вокруг одного фонового потока: запуск/стоп/инъекция наблюдений).
- `deep_self_review` — слэш-команда `/review-skill`, изолированная.
- `supervisor/` (events/queue/workers/git_ops) — отдельная событийная система, не нужна для нашего домена.

Выпил сводится к: удалить 5 файлов, заменить 5-10 точек инжекта в `server.py` на стабы. 1-2 рабочих дня.

## Что выпиливаем

| Модуль | Назначение | Решение |
|---|---|---|
| `ouroboros/consciousness.py` | Background thinking loop | Удалить |
| `ouroboros/reflection.py` | Self-reflection passes | Удалить |
| `ouroboros/deep_self_review.py` | `/review-skill` команда | Удалить |
| `ouroboros/improvement_backlog.py` | Дайджест улучшений из failed reviews | Удалить |
| `ouroboros/consolidator.py` | Memory consolidation | Удалить |
| `ouroboros/review*.py` | Self-review state machine (~100k LOC) | Оценить отдельно (вероятно удалить большую часть) |
| `ouroboros/marketplace*` | Skill marketplace | Удалить (нам не нужен) |
| `ouroboros/a2a_*.py` | Agent-to-Agent protocol | Удалить (нам не нужен) |
| `supervisor/` | Event-driven управление задачами | Оценить отдельно |

## Что оставляем (ядро)

- `loop.py`, `agent.py` — тонкий tool-loop (используем как оркестратор)
- `llm.py`, `provider_models.py`, `pricing.py` — multi-provider роутер (расширим под GigaChat/Cloud.ru)
- `tools/`, `skill_loader.py`, `extension_loader.py`, `contracts/plugin_api.py` — skill-система (наш `neftegaz_analyst` skill пойдёт сюда)
- `safety.py` — sandbox для tools
- `context.py`, `context_compaction.py`, `memory.py` — контекст и память
- `web/`, `server.py` — UI и сервер (минимальный косметический ребрендинг)
- `gateways/` — интеграции (Telegram bridge оставляем как опцию)

## Что добавляем (`nefteboros/`)

```
nefteboros/
├── rag/         # PDF parser, chunker, indexer, retriever (BGE-M3 + Chroma)
├── forecast/    # ARIMA + Prophet, бектест, доверительные интервалы
├── search/      # Brave API + tier-1/tier-2 фильтр + blacklist
├── llm/         # GigaChat-адаптер, Cloud.ru-OpenAI-compatible клиент
├── graphs/      # LangGraph subgraph: classify_intent → route → retrieve → synthesize → validate_citations
├── prompts/     # системный промпт аналитика, prompts evaluation
├── citations/   # пост-валидатор источников (anti-hallucination)
└── bot/         # Telegram-бот на aiogram (отдельный сервис)
```

## Логика приоритизации источников (architectural)

Согласно ТЗ §2.4: RAG (отчёты) → если достаточно, основной источник → web как дополнение или для актуальности → явная маркировка `[Отчёт OPEC MOMR, март 2025]` vs `[Источник: Reuters, web]`.

В нашей реализации: эту логику кодирует LangGraph subgraph (см. ADR-0005, будет создан). Она вызывается из tool'а `analyst_query`, зарегистрированного skill'ом `neftegaz_analyst`. Ouroboros' loop.py остаётся внешним оркестратором — он диспетчеризует tool-calls. Внутри одного tool'а — наш доменный граф.

## Последствия

**Плюсы:**
- Демонстрация глубины: «взяли open-source платформу Сбера, адаптировали под отрасль»
- Полный контроль: можем менять UI, добавлять любые tools, ребрендить
- Один deployment artifact (Docker образ)
- Telegram bridge уже есть в Ouroboros — нам не нужно делать его с нуля (но мы всё равно сделаем свой aiogram-бот для большей гибкости — см. ADR-0006)

**Минусы / риски:**
- Объём кода: оригинальный repo ~80k LOC. Нужно не утонуть в чужой архитектуре.
- Тесты Ouroboros упадут после выпила. Надо чинить или временно скипать выпиленные участки.
- Безопасность ouroboros (`safety.py`, `claude_code_edit` revert) — мощная и сложная, не ломаем.

**Митигации:**
- День 1: explore-фаза, картирование точек сопряжения, фиксация в `docs/architecture.md`
- Каждый PR — изолированный, маленький, с changelog и проходящими тестами
- Безопасность не трогаем — только удаляем потребителей, не саму подсистему

## Альтернативы рассмотренные

- **Гибрид B+C** (наш standalone + опциональный skill): отвергнут из-за дублирования кода и риска "ни вашим ни нашим" впечатления
- **Полный rewrite поверх LangChain без Ouroboros**: отвергнут из-за прямого требования ТЗ

## Ссылки

- ТЗ: [docs/tz/original.md](../tz/original.md)
- Upstream: <https://github.com/joi-lab/ouroboros-desktop>
- Архитектура: [docs/architecture.md](../architecture.md) (TBD в следующем PR)
