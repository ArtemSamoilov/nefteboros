# 2026-05-10 — WSRunner + eval observability flush fix

## Задача

В сегодняшней diagnostic-сессии 2026-05-10 (перед задачей #1 «сценарный прогноз») обнаружилось:

1. **eval_e2e через GraphRunner НЕ создаёт `user_request` traces в Langfuse.** Track F observability привязан к `OuroborosAgent.handle_task` wrap — direct `analyst_graph.ainvoke()` (как в GraphRunner) минует handle_task → root span не создаётся → child observations попадают как orphan top-level traces без parent → невозможно correlate. Из 30 диалогов eval_e2e получилось ~10 orphan observations вместо ожидаемых 30 user_request с детальной иерархией.

2. **Короткие диалоги теряются в Langfuse даже через WS.** Smoke-тест 5 диалогов через WS: 4 из 5 user_request попали в Langfuse, **5-й (out_of_scope `«Какая сегодня погода в Москве?»`) — потерян**. Диагноз: Langfuse SDK 4.x батчит spans асинхронно (по time/size triggers). Refusal-only диалоги (1 LLM round, 1-7 сек, ~200 tokens) закрывают WS connection раньше batch flush'а → trace теряется. Длинные multi-tool диалоги работали за счёт периодического flush'а в batch window.

Задача Артёма: **«Хочу, чтобы все запросы, которые я гоняю на тестовом сете, шли в Langfuse»**. Закрываем оба gap.

## Что сделано

### 1. `WSRunner` в `scripts/eval/eval_e2e.py`

Новый class WSRunner (default runner вместо GraphRunner). Каждый dialogue:

- Открывает WebSocket к `server.py` (default `ws://localhost:8000/ws`, env `EVAL_WS_URL`).
- Отправляет `{type: chat, content: query, sender_session_id, client_message_id}`.
- Принимает stream assistant chunks. Break logic по образцу `verify_track_f_ws.py`: substantial response (chars > 50) → 2s grace → idle recv timeout → break.
- Возвращает `RunResult(answer, tools_called, refused)`.
- `tools_called` извлекается из citations в `answer` через `parse_rag/web/forecast_citations` (детерминированно, без отдельного API call к Langfuse).

Через WS-pipeline каждый dialogue проходит `OuroborosAgent.handle_task` → root user_request trace + child observations (classify_intent, forecast_call, synthesize, web_search, rag_search, validate_citations). Это unified observability: eval test set попадает в Langfuse так же как production user requests.

CLI:

- (default) — `WSRunner` с unified observability.
- `--graph` — legacy GraphRunner (для unit-тестов без server'а).
- `--mock` — MockRunner (smoke без LLM).
- `--limit N` — подмножество для smoke / partial baseline.
- `--ws-url URL` — переопределение endpoint (default `$EVAL_WS_URL` или `ws://localhost:8000/ws`).

Документация в module-docstring обновлена.

### 2. `client.flush()` в handle_task wrapper

`nefteboros/observability/_ouroboros_patches.py` — после `root_span.update(output=...)` вызывается явный `client.flush()`:

```python
try:
    client.flush()
except Exception:
    pass
return result
```

Гарантирует доставку trace в Langfuse независимо от длительности dialogue. Cost: ~100-300ms к latency per request — приемлемо для observability prod-grade.

### 3. Bug fix: `save_run` `relative_to` ValueError

`scripts/eval/eval_e2e.py` `save_run()` падал с `ValueError`, если dataset вне `REPO_ROOT` (например, subset из `/tmp/`). Сегодня этот баг два раза cancelled saving metrics после успешного eval'а. Fallback на absolute path string при `relative_to` failure.

## Smoke verification

### До flush fix

5 диалогов через WSRunner на v2.3.2:
- 4/5 dialogues → user_request trace в Langfuse ✓
- 1/5 (out_of_scope `«Какая сегодня погода в Москве?»`) → НЕТ trace ✗
- Метрики: success=1.0, structure=1.0, citations=0.75, refusal=1.0

### После flush fix

(Будет верифицировано после deploy v2.3.3 — re-smoke 5 диалогов; ожидаем 5/5 в Langfuse.)

## Что НЕ в PR

- **session_id не пробрасывается к Langfuse**: WSRunner отправляет `sender_session_id="eval:{dialogue_id}_{ts}"`, но `handle_task` wrap читает только `task.get("chat_id")` и формирует session_id как `chat:{chat_id}`. Server.py видимо игнорирует `sender_session_id` для отображения в trace (но создаёт уникальный `task_id`). Filter eval-traces сейчас — по timestamp window. Workaround `[EVAL]`-префикс в query (отдельный PR если нужно чисто отделять).

- **Параллельный run** диалогов в eval — sequential (один WS connection = один dialogue). Параллелизм отложен (LLM rate limits, WS backpressure).

- **Re-baseline 100 диалогов через WSRunner** — отдельный run (1.5+ часа sequential). Артефакт: новый `metrics/runs/` файл с актуальными метриками + 100 user_request traces в Langfuse за один временной интервал.

## Связанные

- Track F (PR #51) — observability через Langfuse, основная основа для wrap'а.
- Diagnostic session 2026-05-10 — нашла оба gap (orphan observations + flush race).
- ADR-0024 §A4 — observability scope.
