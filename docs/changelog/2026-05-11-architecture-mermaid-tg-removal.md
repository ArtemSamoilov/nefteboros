# 2026-05-11 — architecture.md: mermaid fix + удаление Telegram + ADR-ссылка в коде

**PR:** `fix/architecture-mermaid-tg-adr-ref`
**Связано:** [docs/REPORT.md](../REPORT.md), [README.md](../../README.md), [ADR-0024-observability-langfuse](../adr/0024-observability-langfuse.md).

## Задача

Три проблемы в `docs/architecture.md`, обнаруженные при ревью deliverable перед сдачей:

1. **Mermaid-диаграмма не рендерится на GitHub** (parse error на `'LINK_ID'`). Причина — `<br/>` внутри `[(...)]` и `[...]` узлов без кавычек. GitHub mermaid-парсер строго относится к специальным символам в неквотированных лейблах.
2. **Telegram-бот упомянут в диаграмме и компонентной таблице**, хотя к решению отношения не имеет (deploy через web, бот не работает на Timeweb из-за RKN). Согласовано с координатором — TG не упоминаем в архитектурном описании.
3. **Опечатка в комментарии кода** `nefteboros/graphs/analyst_graph.py:4`: ссылка на `ADR-0025 (observability)` — но `0025` занят `0025-readme-modules-structure.md` (новый ADR из PR #60). По смыслу должен быть `0024-observability-langfuse.md`.

## Что сделано

### docs/architecture.md
- Все mermaid-лейблы с `<br/>` обёрнуты в кавычки или развёрнуты в одну строку через ` — `. Конкретные узлы:
  - `OurLoop`, `ChromaDB`, `Brave`, `OU` — кавычки + em-dash separator.
  - Удалена ветка `User -->|Telegram| Bot` + узел `Bot` + грань `Bot --> Core`.
- Sequence-диаграмма: `participant UI as Web/Telegram UI` → `participant UI as Web UI`.
- Компонентная таблица: удалены строки `Telegram bridge` и `TG bot (свой)`. Также обновлены устаревшие `TBD` на `Реализован` для: `Eval/Скрипты`, `Eval/Датасеты`, `Eval/Метрики`, `Деплой/Docker`, `Skill/neftegaz_analyst`. Forecast статус расширен ссылкой на ADR-0024-ou-regime.
- Sync LLM router в high-level диаграмме с реальностью (PR #57 aitunnel, ADR-0007):
  - `Cloud.ru` (устаревшее имя) → `Hydra`.
  - Добавлен узел `AItunnel — резервный провайдер`.
  - GigaChat-роль уточнена: `GigaChat-2-Max — llm_disambiguate`.
- Forecast-узел `ARIMA / Prophet` заменён на `OU regime` (в соответствии с ADR-0024-ou-regime-forecast, действующее решение в проде).
- `Lang detection` → `определение языка` (русификация в духе REPORT v3).
- Observability-секция: ссылка `(ADR-0024)` уточнена в `ADR-0024-observability-langfuse` для устранения двусмысленности (коллизия номеров 0024).

### Опечатки `ADR-0025` в коде (11 мест в 6 файлах)

Сквозная опечатка: ссылки на несуществующий `0025-observability-langfuse.md`. По содержанию должен быть `0024-observability-langfuse.md`. Указан полный suffix файла из-за коллизии 0024 (есть `0024-ou-regime-forecast.md` и `0024-observability-langfuse.md`).

- `nefteboros/graphs/analyst_graph.py:4` (module docstring)
- `nefteboros/graphs/analyst_graph.py:111` (комментарий wrap-логики)
- `nefteboros/graphs/analyst_graph.py:167` (Trace lifecycle ссылка)
- `nefteboros/graphs/nodes/synthesize.py:150` (Observability docstring)
- `nefteboros/graphs/nodes/synthesize.py:183` (tokens/cost комментарий)
- `nefteboros/graphs/nodes/llm_disambiguate.py:162` (Known limitations ссылка)
- `nefteboros/graphs/nodes/llm_disambiguate.py:178` (usage_metadata комментарий)
- `nefteboros/observability/__init__.py:3` (с broken path к несуществующему файлу)
- `nefteboros/observability/__init__.py:135` (комментарий к декоратору)
- `nefteboros/observability/tracer.py:3` (docstring)
- `nefteboros/observability/cost.py:3` (иерархия cost calculation)

## Что НЕ в PR

- Коллизии номеров ADR (0016 и 0024) — backlog, отдельная задача по ренеймингу со сквозной правкой ссылок.
- Полный аудит остальных устаревших claim'ов в архитектуре (например, статус `consolidator` в «Выпиливаем» — фактически не выпилен, есть в коде без инструментации) — отдельная задача maintenance.
- Дизайн новых диаграмм для observability flow / async event flow — план v2.4.

## Тест

- Открыть [docs/architecture.md](../architecture.md) на GitHub после merge — оба mermaid-блока рендерятся без ошибок parse.
- `grep -i "telegram\|тгбот" docs/architecture.md` → пусто.
- `grep "ADR-0025" nefteboros/` → пусто (опечатка устранена).
- Сверить с `docs/REPORT.md §2` (архитектура) — нет противоречий по компонентам.
