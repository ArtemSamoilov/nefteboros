# Модуль: Consolidator (Ouroboros memory)

Block-wise суммаризация истории чата для экономии context window. Не часть analyst graph'а — это фоновый процесс из Ouroboros core, который ужимает «давно прошедшие» сообщения в саммари.

## Точка входа

- `ouroboros/consolidator.py:76` — `consolidate(history, …)`.
- Запускается фоном (без явного триггера от user) в Ouroboros loop.

## Поток

1. Берёт «старую» часть истории (за пределами active window).
2. Группирует сообщения в блоки фиксированного размера.
3. Зовёт LLM (env-driven `OUROBOROS_MODEL_LIGHT` → `OUROBOROS_MODEL`, см. changelog [2026-05-11-observability-post-span-flush.md](../changelog/2026-05-11-observability-post-span-flush.md) §«ouroboros/consolidator.py — env-driven CONSOLIDATION_MODEL»).
4. Записывает summary block, исходные сообщения помечает как «consolidated».

## Входы / выходы

**Вход:** chat history (`list[Message]`).

**Выход:** обновлённая history с компрессией старых блоков.

## Ключевые ADR

- Отсутствуют. Это upstream Ouroboros механика, оставленная при форке (см. [ADR-0001](../adr/0001-fork-ouroboros.md), список «оставлено» — tool-loop, multi-provider router, web UI и т. д.).

## Метрики

**Инструментация: НЕТ.** Consolidator не обёрнут `@observe`, не пишет в Langfuse, не имеет JSON-trace span'ов.

**Это известный gap** (`docs/changelog/2026-05-11-observability-post-span-flush.md` §«Orphan tool traces из background tasks»): если tool вызывается из background scheduler / consolidator, минуя `handle_task` wrap, span создаётся как orphan root trace (`session=None`) или вовсе не пишется. Fix отложен в backlog v2.4.

**Eval: НЕТ.** Consolidator не покрыт регулярными eval-прогонами. Качество саммари не измеряется в metrics. Это сигнал координатору: если оценщик попросит «как вы знаете, что consolidator не теряет ключевые факты из истории» — ответа нет, нужна отдельная задача.

## Известные ограничения

- Модель консолидации до PR 2026-05-11 была hardcoded `google/gemini-3-flash-preview` (OpenRouter-only) — на prod без `OPENROUTER_API_KEY` падал каждые 1-2 мин с `All models are down`. Сейчас env-driven, дефолт fallback на primary `OUROBOROS_MODEL` — см. changelog.
- Background ошибки в consolidator до 2026-05-11 ломали observability state на всех последующих requests в PID через double-yield в `remote_parent_cm` (исправлено в том же PR).
- При высокой нагрузке consolidator может конкурировать за LLM tokens с основным аналитиком (общий quota).
