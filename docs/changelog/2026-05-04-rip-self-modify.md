# 2026-05-04 — Rip self-modify subsystems (feature/rip-self-modify)

## Задача

Удалить из форка Ouroboros подсистемы самомодификации, которые не нужны для отраслевого ассистента. Получить чистое ядро (loop + agent + tools + LLM router + skill system + safety) без autopoesis-логики (background consciousness, reflection-between-tasks, deep self-review, improvement backlog).

## Контекст

Согласно [ADR-0001](../adr/0001-fork-ouroboros.md), Ouroboros — это самомодифицирующийся agent-runtime. Для нашей задачи (нефтегазовый аналитик) self-modify подсистемы лишние и потенциально опасные (агент не должен переписывать сам себя, давая ответы Грефу).

Анализ импортов показал, что сцепленность — bolt-on: ядро (`loop.py`, `agent.py`, `context.py`) импортирует self-modify модули в нескольких изолированных точках, не пронизывает всё. Полный rip реален.

## Что сделано

### Удалены модули (4 файла)
- `ouroboros/consciousness.py` (30 KB) — фоновая нитка `BackgroundConsciousness`
- `ouroboros/reflection.py` (18 KB) — рефлексия между задачами + `_update_patterns`
- `ouroboros/deep_self_review.py` (13 KB) — слэш-команда `/review` для самоаудита
- `ouroboros/improvement_backlog.py` (7 KB) — дайджест нерешённых улучшений в контекст

### Удалены тесты (7 файлов)
- `test_consciousness.py`, `test_deep_self_review.py`, `test_improvement_backlog.py` — тесты удалённых модулей
- `test_evolution_status.py`, `test_repo_read_limits.py` — тесты, целиком зависящие от `BackgroundConsciousness`
- `test_process_memory.py` — целиком про `reflection`
- `test_block1_review_pipeline.py` — целиком про deep_self_review pipeline

### Точечно удалены тесты внутри файлов
- `test_max_tokens_constants.py` — удалены `test_reflection_generate_max_tokens`, `test_consciousness_max_tokens`, `test_deep_self_review_budget_limit`
- `test_budget_tracking.py` — удалены `TestReflectionCostTracking`, `TestUpdatePatternsCostTracking`
- `test_context_memory_overhaul.py` — удалён `test_health_invariants_come_first_in_background_consciousness_context`

### Правки в core (8 файлов)
- `server.py`:
  - `_consciousness` глобал, `BackgroundConsciousness` import — удалены
  - `_describe_bg_consciousness_state()` — упрощена до always-disabled (UI-shim)
  - `/review` команда — заменена на отказное сообщение
  - `/bg` команда — заменена на отказное сообщение
  - Owner-message handler — удалены `consciousness.inject_observation/pause/resume` calls
  - `_run_supervisor`: удалены инициализация `BackgroundConsciousness`, авторестор, поле `consciousness=` в `_event_ctx`, импорт `queue_deep_self_review_task`
  - `_execute_panic_stop` — вызывается с `None` вместо `ctx.consciousness`
- `ouroboros/context.py`:
  - Удалён блок prompt-runtime drift check (использовал `BackgroundConsciousness._BG_TOOL_WHITELIST`)
  - Удалён `format_backlog_digest` блок в context builder
- `ouroboros/agent.py`: удалена ветка `if task_type_str == "deep_self_review":`
- `ouroboros/agent_task_pipeline.py`: удалены функции `_update_improvement_backlog`, `_run_reflection`, и их вызовы в `_run_post_task_processing_async`
- `ouroboros/tools/control.py`: удалены функции `_request_deep_self_review`, `_toggle_consciousness` + их `ToolEntry` регистрации
- `ouroboros/safety.py`: удалены policy entries для `request_deep_self_review`, `toggle_consciousness`
- `supervisor/events.py`: удалены `_handle_toggle_consciousness`, `_handle_deep_self_review_request` + их registry entries
- `supervisor/queue.py`: удалена функция `queue_deep_self_review_task`, удалены условия `task_type == "deep_self_review"` в `_task_priority` и timeout overrides

### Что НЕ тронуто (намеренно)
- `ouroboros/consolidator.py` — используется в `context.py` (миграция формата истории) и `agent_task_pipeline.py` (memory consolidation). Это про память, не про self-modify в строгом смысле. Если решим выпиливать — отдельным PR.
- `marketplace*`, `a2a_*` — отдельный PR (не относятся к self-modify напрямую).
- Evolution mode (`/evolve`, `toggle_evolution`) — отдельный PR (это ещё одна форма автономной модификации, заслуживает обдуманного решения).
- `safety.py` остальная логика — sandbox, policy LLM check, claude_code_edit revert — оставлены без изменений.

## Тесты

После выпила Python AST-парсинг прошёл для всех затронутых файлов:
- `agent.py`, `agent_task_pipeline.py`, `context.py`, `safety.py`, `tools/control.py`, `server.py`, `supervisor/events.py`, `supervisor/queue.py` — все компилируются.
- Полный pytest прогон не делался в этом PR (нет venv в репо). Будет в `feature/ci-smoke` PR.

## Известные ограничения / TODO

- `if True:` оставлен в `agent.py` после удаления `if/else` блока — отступ тела сохранён ради минимального диффа. Можно убрать при следующем рефакторинге `agent.py`.
- В `tests/test_a2a_protocol.py` остались импорты из `consolidator` — НЕ удалены, потому что `consolidator.py` не выпилен.
- Smoke-тест на запуск `server.py` (uvicorn-up) — отдельный PR `feature/ci-smoke`.

## Файлы

**Удалены (11):**
- `ouroboros/consciousness.py`, `ouroboros/reflection.py`, `ouroboros/deep_self_review.py`, `ouroboros/improvement_backlog.py`
- `tests/test_consciousness.py`, `tests/test_deep_self_review.py`, `tests/test_improvement_backlog.py`, `tests/test_evolution_status.py`, `tests/test_repo_read_limits.py`, `tests/test_process_memory.py`, `tests/test_block1_review_pipeline.py`

**Изменены (11):**
- `server.py`, `ouroboros/context.py`, `ouroboros/agent.py`, `ouroboros/agent_task_pipeline.py`, `ouroboros/tools/control.py`, `ouroboros/safety.py`
- `supervisor/events.py`, `supervisor/queue.py`
- `tests/test_smoke.py`, `tests/test_max_tokens_constants.py`, `tests/test_budget_tracking.py`, `tests/test_context_memory_overhaul.py`

**Итого:** ~70 KB удалённого кода, ~12 KB изменений в core.

## Связанные документы

- ADR-0001: [docs/adr/0001-fork-ouroboros.md](../adr/0001-fork-ouroboros.md) — решение о форке
- Архитектура: [docs/architecture.md](../architecture.md)
