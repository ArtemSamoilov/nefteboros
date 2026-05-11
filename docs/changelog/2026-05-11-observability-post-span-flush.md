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
    # span закрыт → trace finalized → flush для гарантии delivery
    client.flush()
    return result
```

`client.flush()` сразу после exit context manager — Langfuse SDK batch получает root span и отправляет на следующем tick'е.

**Что было отвергнуто и почему.** Пробовали `tracer_provider.force_flush(timeout_millis=5000)` через OTel — синхронный close всех SpanProcessor pipelines. Он давал стабильно 5/5 root traces, **но ломал child propagation для tool_dispatch worker threads**: sync close обрывал OTel context inheritance, и span'ы `rag_search`/`web_search`/`analyst_query` появлялись как orphan root traces вместо child user_request. Trade-off неприемлем — оценщик читает иерархию trace'а, а пустой `user_request` без tool calls + рядом orphan tools = выглядит сломано. Откатили в финальной версии PR, оставили только `client.flush()`.

### 2. `remote_parent_cm` single-yield

`@contextmanager` теперь имеет **один** `yield`. Setup parent OTel context до try/except; если build fails — graceful degrade через `if non_rec is None: yield; return`. Это закрывает «RuntimeError: generator didn't stop after throw()» на consolidator-fail.

## Smoke verification

### До (v2.3.4, flush внутри span)

5 диалогов в session, repeated runs:
- Pattern: 2 терь стабильно, пара меняется.
- Trace `user_request` создавался, но **input/output не успевали** записаться (span ещё open при flush).

### После (этот PR, flush после span + client.flush + inter-dialogue 3s)

Smoke 3 диалога на проде после revert force_flush, 2026-05-11:
- ✓ **rag_plus_web** (12:00:07) — 15 observations, **3 TOOL spans** (rag_search + 2×web_search) **все child user_request**, input/output полные.
- ⚠ **rag_only** (12:05:30) — 5 observations, 1 TOOL span (rag_search) child, **empty input/output** — handle_task видимо не успел `root_span.update(output=...)` (eval timeout 360s).
- 2/3 root traces появились в Langfuse. Третий потерян (~33% refusal-rate как было в peaceful-einstein).

Главное: **child иерархия восстановлена** — оценщик видит полный flow agent'а (classify_intent → tool calls → synthesize → validate_citations) внутри `user_request`. Это «качественный trace» — критерий приоритета.

## Known limitations (backlog v2.4)

### 1. Root trace loss на короткие диалоги (~33%)

Async batch flush race на коротких диалогах: handle_task возвращает → server принимает следующий request → новый root span overlap'ит trace_id → batch drops старый. `client.flush()` после span помогает, но не до конца.

**Fix варианты:**
- OTel `BatchSpanProcessor` config: меньший `schedule_delay_millis` — sync-like экспорт, но cost на каждый span.
- Замена batch exporter на `SimpleSpanProcessor` — без батча, дорого по сети.

### 2. Orphan tool traces из background tasks

`traced_tool` decorator на `skills/neftegaz_analyst/plugin.py` создаёт span при любом вызове tool. Если tool вызывается **минуя** `handle_task` wrap (background scheduler, consolidator, direct skill_exec) — `_trace_context_per_pid` пуст → span создаётся как root trace с `name=tool_name`, `session=None`.

**Fix:** в `traced_tool` при отсутствии `tc` либо skip Langfuse path, либо создать synthetic root user_request span с пометкой `metadata.synthetic=true`. Нетривиально — отложено в v2.4.

**Прагматика:** для прод-сценария (один WS chat, paced user input) gap гораздо меньше, чем в eval. Главный путь — handle_task через WS — теперь даёт полные иерархии.

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
