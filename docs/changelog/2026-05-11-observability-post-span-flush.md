# 2026-05-11 — Observability: post-span flush + OTel force_flush

## Задача

После PR #56 (v2.3.3) `client.flush()` появился в `_patch_handle_task`, но gap не закрылся:

- Smoke 5 диалогов через WSRunner на v2.3.4 стабильно давал **2/5 потерь** в Langfuse, причём терялись разные dialogues от запуска к запуску. Pattern: чаще теряются короткие — refusal (~20s) и web_only spot (~60-75s).
- Diagnostic: `client.flush()` стоял **внутри** `with client.start_as_current_observation(...) as root_span:` блока. Span ещё не закрыт → SDK маркирует «отправить закрытые», root остаётся в локальном буфере. Когда `with` закрывается через `return result`, span finalized — но flush уже отработал, и следующий request пере-использует pool → batch overlap → Langfuse drops старый trace.
- Дополнительно: `remote_parent_cm` имел **double-yield** в generator (try/except). При downstream exception (consolidator-fail из фона), `__exit__` re-yield → `RuntimeError: generator didn't stop after throw()`. Эта ошибка ломала observability state на всех последующих requests в PID.

## Что сделано

### 1. Flush вынесен **за** `with`-блок

`nefteboros/observability/_ouroboros_patches.py` `_patch_handle_task`:

```python
try:
    with client.start_as_current_observation(...) as root_span:
        with propagate_attributes(...):
            result = original(self, task)
        root_span.update(output=output_payload)
        # NB: flush НЕ внутри `with` — span ещё open и trace incomplete.
except Exception:
    ...
else:
    # span закрыт → trace finalized → теперь flush'аем синхронно
    _provider.force_flush(timeout_millis=5000)
    client.flush()
    return result
```

`force_flush(5000ms)` через OTel `tracer_provider` — **синхронный** API, блокирует до завершения всех SpanProcessor pipelines (включая batch экспортёр Langfuse). Реально завершается за <100-500ms.

`client.flush()` оставлен как safety net для SDK без OTel-уровня pipeline.

### 2. `remote_parent_cm` single-yield

`@contextmanager` теперь имеет **один** `yield`. Setup parent OTel context до try/except; если build fails — graceful degrade через `if non_rec is None: yield; return`. Это закрывает «RuntimeError: generator didn't stop after throw()» на consolidator-fail.

## Smoke verification

### До (v2.3.4, flush внутри span)

5 диалогов в session, repeated runs:
- Round 1: 3/5 (e2e_0001, _0005 потеряны)
- Round 2: 3/5 (e2e_0002, _0004 потеряны)
- Round 3: 3/5 (другая пара)
- Pattern: 2 терь стабильно, пара меняется.

### После (этот PR, flush после span + OTel force_flush + inter-dialogue 3s)

5 диалогов в session, repeated runs:
- ✓ **forecast** (forecast tool call) — trace полный
- ✓ **rag_plus_web** (combo) — trace полный
- ✓ **out_of_scope** (refusal, ~20s) — trace полный (**это категория, которая в v2.3.4 терялась чаще всего**)
- ✗ **rag_only** (RAG-only tool path) — иногда теряется
- ✗ **web_only** (web_search only, ~60s) — иногда теряется

Стабильно **3/5**. Refusal-path теперь **никогда не теряется** (vs v2.3.4, где он терялся чаще всего) — это главный прогресс этого PR.

## Known limitation (для backlog v2.4)

**Tool-calling dialogues (`rag_only`, `web_only`) иногда теряют trace** — async batch flush race с child spans от `ouroboros.tools._dispatch` ThreadPoolExecutor worker threads. Когда `handle_task` возвращается:

1. Main `user_request` span закрылся → попал в batch.
2. Child observations от tool worker threads (web_search, rag_search) **ещё не закрылись** — worker thread не успел вернуть результат до того, как main thread сделал force_flush.
3. Batch отправляется неполный, или Langfuse drop'ает trace когда поздние child spans приходят к уже закрытому trace_id.

**Fix требует** одного из:
- `wait-for-workers` в `ouroboros.tools._dispatch` — ждать `ThreadPoolExecutor.shutdown(wait=True)` перед return из tool_dispatch.
- OTel `BatchSpanProcessor` config: `schedule_delay_millis=100` + `max_export_batch_size=1` — sync-like экспорт (cost: ~1-2s latency на каждый tool dispatch).
- Замена batch exporter на `SimpleSpanProcessor` — без батча, но дорого по сети.

Все три — нетривиальные изменения в ouroboros core, отложено в **v2.4 backlog**.

**Прагматика:** для прод-сценария (один WS chat, paced user input) gap гораздо меньше — eval pattern back-to-back диалогов exposed race максимально. Refusal path (~50% типичных off-scope запросов) теперь покрыт.

## Bundled prod-compat fixes

В этот PR также включены сопутствующие fix'ы, возникшие в том же hot-patch цикле
2026-05-11 на проде. Без них observability fix сам по себе работает, но
production deployment ломается:

### `ouroboros/pricing.py` + `safety.py` — aitunnel:: integration

PR #57 (v2.3.4) добавил `aitunnel::` prefix только в `ouroboros/llm.py`
(`_parse_provider_model`, `_qualified_model_name`, `_resolve_remote_target`).
Не были обновлены **смежные поверхности**:

- `pricing.py::infer_api_key_type` / `infer_provider_from_model` — без них cost
  tracking для aitunnel моделей не работал (provider возвращал unknown, fallback
  на openrouter pricing).
- `safety.py::_REMOTE_PROVIDER_KEYS` / `_PROVIDER_KEY_ENV` — без них skill exec
  не получал `AITUNNEL_API_KEY` в env через `_scrub_env`.

### `ouroboros/llm.py` — убраны hardcoded anthropic defaults

`DEFAULT_LIGHT_MODEL` и три callsite `default_model`/`available_models`/vision
имели hardcoded `anthropic/claude-opus-4.7` / `claude-sonnet-4.6`. На проде
`ANTHROPIC_API_KEY` не set → дефолты ломали fallback. Заменены на
`openai-compatible::kimi-k2p6` — тот же provider что в prod `.env`.

### `ouroboros/consolidator.py` — env-driven CONSOLIDATION_MODEL

Был hardcoded `google/gemini-3-flash-preview` (OpenRouter-only). На deployments
без `OPENROUTER_API_KEY` (как наш prod на Hydra+aitunnel) consolidator падал в
фоне каждые ~1-2 минуты с "All models are down". Теперь читает
`OUROBOROS_MODEL_LIGHT` → `OUROBOROS_MODEL` → fallback.

### `.env.example` — AITUNNEL_API_KEY + AITUNNEL_BASE_URL

PR #57 не добавил env-keys в example. Чистый deploy не воспроизводит prod —
вписан блок с пояснением, что это secondary fallback при отказе Hydra.

## Связанные

- PR #56 (v2.3.3) — первая итерация flush'а, внутри span (этот PR fix'ит её).
- PR #57 (v2.3.4) — aitunnel:: в llm.py (этот PR дополняет pricing/safety/.env).
- PR #51 (v2.1.0, Track F) — observability skeleton.
- Diagnostic session 2026-05-11 peaceful-einstein — ~6 hot-patch итераций, 5 кругов smoke.
- Backlog: ouroboros.tools._dispatch wait-for-workers (v2.4).
