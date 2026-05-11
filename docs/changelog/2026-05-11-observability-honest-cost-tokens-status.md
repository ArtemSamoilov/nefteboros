# 2026-05-11 — honest fix: cost / tokens / model name в Langfuse generations — null

**PR:** `fix/observability-honest-cost-tokens-status`
**Связано:** [docs/REPORT.md](../REPORT.md), [docs/architecture.md](../architecture.md), [docs/modules/analyst_graph.md](../modules/analyst_graph.md), [docs/modules/routing.md](../modules/routing.md), [ADR-0024-observability-langfuse](../adr/0024-observability-langfuse.md).

## Расхождение

В deliverable документации (`docs/REPORT.md §3`, `docs/architecture.md` секция Observability, `docs/modules/analyst_graph.md`, `docs/modules/routing.md`) заявлено, что **токены / стоимость / задержка** считаются через `log_llm_usage` с иерархией `COST_RATES → ouroboros.pricing.estimate_cost → null`.

Фактически в Langfuse Cloud:
- ✅ **Латентность** каждого узла — фиксируется (verified: 106.306s, 48.395s, 18.069s в дампе recent generations)
- ✅ **Иерархия trace** `user_request → дочерние spans` — работает
- ✅ **Статус выполнения** — фиксируется
- ❌ **`provided_model_name`** — `null` во всех 10 проверенных recent generations
- ❌ **`usage_details`** — `null`
- ❌ **`total_cost`** — `null`

Verified: дамп 10 generations через `langfuse.api.observations.get_many(type="GENERATION", limit=10)`. Поля `provided_model_name`, `usage_details`, `total_cost` пусты на всех. `log_llm_usage` в `synthesize._call_llm` и `llm_disambiguate._call_llm` вызывается, но enrichment до spans не доходит — gap в `nefteboros/observability/_observe.py` или patches.

## Что сделано

Документация приведена к реальности — без перeобещаний. Конкретно:

- **`docs/REPORT.md §3`** (Observability bullet): убрана фраза «Токены / стоимость / задержка считаются через `log_llm_usage` с иерархией: наши `COST_RATES` ... → null». Заменено на: «**Латентность** каждого узла и **статус выполнения** фиксируются автоматически».
- **`docs/REPORT.md §4`** (Ограничения): добавлен bullet «Cost / tokens / model name в Langfuse generations — не обогащаются. … Полная реализация — в плане v2.4».
- **`docs/architecture.md`** Observability секция: тот же honest reframing + явный bullet про backlog v2.4.
- **`docs/modules/analyst_graph.md`** таблица узлов: колонка «Где cost / tokens» → «Что зафиксировано в Langfuse» с указанием null для cost/tokens/model.
- **`docs/modules/routing.md`** `llm_disambiguate` bullet: явное упоминание enrichment-gap.

## Что НЕ тронуто

- `docs/adr/0024-observability-langfuse.md` — это **design-intent** документ, фиксирует решение на момент принятия. Реальность ушла от него — следует обновить отдельным ADR'ом или add'нуть «Update 2026-05-11» в самом конце, но это уже maintenance scope, не часть текущей сдачи.
- `log_llm_usage` функция в коде (`nefteboros/observability/_observe.py` и др.) — не трогаем, согласовано с координатором. Код менять не будем, только документация приведена в соответствие с фактом.

## Что в backlog v2.4

- Enrichment `provided_model_name` / `usage_details` / `total_cost` в Langfuse generations — `langfuse.update_current_observation(model=..., usage=..., cost_details=...)` в `log_llm_usage` или в `_observe.py` обёртке.
- Обновление ADR-0024 либо новый ADR со снапшотом фактического состояния observability.
