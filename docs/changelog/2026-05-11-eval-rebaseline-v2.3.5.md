# 2026-05-11 — E2E re-baseline на v2.3.5+

## Задача

Координатор: «перезапустить eval_e2e на 100 диалогах на актуальной версии (v2.3.5+) через WSRunner, сравнить с baseline v1.0, зафиксировать в `docs/eval-results-v2.3.5.md`». Результаты пойдут в таблицу REPORT.md (другая сессия) и в README (третья сессия).

В `2026-05-10-wsrunner-eval-observability.md` уже была отмечена эта работа как «отдельный run, 1.5+ часа sequential» — этот PR её закрывает.

## Что сделано

### 1. Прогон eval_e2e на 100 диалогах через WSRunner

- HEAD: `c3c22f6` (PR #48 `auto-enable-web-search`, поверх v2.3.5 `ba783d4`).
- Окружение: local macOS, Python 3.12.12 (`uv venv -p 3.12`), server.py на `127.0.0.1:8000`.
- Provider config: prod-aligned через `scripts/deploy/apply_production_config.py` (PRIMARY=kimi-k2p6 / ROUTING=GigaChat-2-Max, auto-enable `neftegaz_analyst:analyst_query,rag_search`).
- RAG: `NEFTEBOROS_RAG_VECTORSTORE_PATH` указывает на полный parent-repo vectorstore (collection `nefteboros_corpus_v2_heading`). Worktree-local `data/` использовать нельзя — пустой.
- Старт: 2026-05-11 20:23:59 MSK, финиш: 2026-05-12 07:00:20 MSK, **прогон 10 ч 36 мин**.
- Артефакт: `metrics/runs/2026-05-12T04-00-20Z_e2e_ws_c3c22f6.json`.

### 2. Incremental checkpoint в `scripts/eval/eval_e2e.py`

Первая попытка прогона умерла на 91/100 (предположительно SIGHUP / интернет-провал) без сохранения итогового JSON. Чтобы не зависеть от случайностей на 10-часовом прогоне, в `_run_all` добавлен `checkpoint_cb` (вызывается каждые 10 диалогов, кроме последнего), а в `save_run` — параметр `partial_done` (суффикс `_partial_NNN` в имени файла, чтобы не пересекаться с финалом).

Изменения локализованы в `scripts/eval/eval_e2e.py` (eval-инструмент, не в scope «код пайплайнов/скиллов»):

```python
# _run_all
async def _run_all(runner, dialogues, *, checkpoint_cb=None, checkpoint_every: int = 10):
    ...
    if checkpoint_cb is not None and done % checkpoint_every == 0 and done < len(dialogues):
        try: checkpoint_cb(list(scores), done)
        except Exception: logger.exception("checkpoint_cb failed at %d", done)

# save_run
def save_run(..., partial_done: Optional[int] = None) -> Path:
    suffix = f"_partial_{partial_done:03d}" if partial_done is not None else ""
    out_path = METRICS_RUNS_DIR / f"{timestamp}_e2e_{runner_name}_{commit}{suffix}.json"
```

Перезапуск делает detached double-fork (`( nohup … & )` + `stdin=/dev/null`) чтобы родительский shell не утащил процесс SIGHUP'ом.

### 3. Документ результата + changelog

- `docs/eval-results-v2.3.5.md` — основной technical-документ: шапка, таблицы (all / dev / held_out / by_scenario), регрессии (>5pp), улучшения, технические замечания, известные баги.
- Этот changelog.

## Результат

Главные цифры на 100 диалогах против baseline v1.0 (0.568 / 0.181 / 0.528 / 0.947):

| Metric | v1.0 | v2.3.5+ all | Δ | v2.3.5+ ok-only (n=57) | Δ |
|---|---|---|---|---|---|
| success | 0.568 | **0.344** | −22.4pp | **0.660** | +9.2pp |
| citation | 0.181 | **0.362** | +18.1pp | 0.362 | +18.1pp |
| structure | 0.528 | **0.489** | −3.9pp | 0.489 | −3.9pp |
| refusal | 0.947 | **0.368** | −57.9pp | 0.700 | −24.7pp |

**Главная находка** — `43/100 диалогов в timeout > 360s, концентрированные в хвосте (e2e_0073–0100, почти сплошь)`. На «отвечающих» 57 диалогах success и cite уже выше baseline, structure ≈ baseline; refusal стабильно ниже (но n=10 — слабая статистика). Подозрение на memory/connection leak в server.py / Ouroboros agent loop / Langfuse JSONL tracer.

**Citation удвоился** (0.181 → 0.362) на той же выборке — самое устойчивое улучшение. По сценариям forecast n=16 показал лучший результат по всем трём метрикам (success=0.6 / cite=0.7 / struct=0.8).

## Что НЕ в PR

- **Регрессии не фиксятся.** По указанию координатора: «НЕ фикси регрессии в коде. Это задача отдельная, координатор решит, что с ними делать после.»
- **Valid trace ratio в Langfuse — N/A.** `LANGFUSE_PUBLIC_KEY/SECRET_KEY` в локальной `.env` не настроены, Cloud отключён. Проблема «~33% root trace loss на back-to-back» из changelog'а 2026-05-10 в этом прогоне не верифицирована.
- **JSONL tracer на длинный прогон.** Tracer создал только 1 dir (от smoke), полный прогон в JSONL не записан — отдельный observability-баг, отложен.
- **README / REPORT.md / examples / docs/modules** — off-limits по заданию.

## Связанные

- `2026-05-10-wsrunner-eval-observability.md` — WSRunner + post-span flush, родительская задача.
- PR #36 (D-base baseline, память `feedback_d_base_baseline.md`) — источник цифр v1.0 baseline и каталога известных багов.
- PR #58 (v2.3.5) — `force_flush revert + post-span flush prod compat`, попал в прогон через HEAD.
- PR #48 (`fix/auto-enable-web-search`) — единственный коммит поверх v2.3.5 на HEAD `c3c22f6`.
