# Модуль: Consolidator (память Ouroboros)

Блочная суммаризация истории чата для экономии контекстного окна. Не часть analyst graph'а — это фоновый процесс из ядра Ouroboros, который ужимает «давно прошедшие» сообщения в саммари.

## Точка входа

- `ouroboros/consolidator.py:76` — `consolidate(history, …)`.
- Запускается фоном (без явного триггера от пользователя) в основном цикле Ouroboros.

## Поток

1. Берёт «старую» часть истории (за пределами активного окна).
2. Группирует сообщения в блоки фиксированного размера.
3. Зовёт LLM (env-driven `OUROBOROS_MODEL_LIGHT` → `OUROBOROS_MODEL`, см. changelog [2026-05-11-observability-post-span-flush.md](../changelog/2026-05-11-observability-post-span-flush.md) §«ouroboros/consolidator.py — env-driven CONSOLIDATION_MODEL»).
4. Записывает блок-саммари, исходные сообщения помечает как «consolidated».

## Входы / выходы

**Вход:** история чата (`list[Message]`).

**Выход:** обновлённая история с компрессией старых блоков.

## Ключевые ADR

Отсутствуют. Это апстрим-механика Ouroboros, оставленная при форке (см. [ADR-0001](../adr/0001-fork-ouroboros.md), список «оставлено» — инструментальный цикл, маршрутизатор по нескольким провайдерам, веб-интерфейс и т. д.).

## Метрики

**Инструментация: НЕТ.** Consolidator не обёрнут `@observe`, не пишет в Langfuse, не имеет span'ов в JSON-трейсе.

**Это известный gap** (`docs/changelog/2026-05-11-observability-post-span-flush.md` §«Orphan tool traces из background tasks»): если инструмент вызывается из фонового планировщика или consolidator'а, минуя `handle_task`, span создаётся как осиротевший корневой трейс (`session=None`) или вовсе не пишется. Fix отложен в бэклог v2.4.

**Eval: НЕТ.** Consolidator не покрыт регулярными eval-прогонами. Качество саммари не измеряется. Это сигнал координатору: если оценщик попросит «как вы знаете, что consolidator не теряет ключевые факты из истории» — ответа нет, нужна отдельная задача.

## Известные ограничения

- Модель консолидации до PR 2026-05-11 была hardcoded `google/gemini-3-flash-preview` (только OpenRouter) — на prod без `OPENROUTER_API_KEY` падал каждые 1-2 минуты с `All models are down`. Сейчас env-driven, по умолчанию идёт fallback на основную `OUROBOROS_MODEL` — см. changelog.
- Фоновые ошибки в consolidator'е до 2026-05-11 ломали наблюдаемость на всех последующих запросах в PID через двойной yield в `remote_parent_cm` (исправлено в том же PR).
- При высокой нагрузке consolidator может конкурировать за tokens LLM с основным аналитиком (общая квота).
