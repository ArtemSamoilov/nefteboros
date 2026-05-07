# Changelog: chore(llm) — глобальный `max_tokens` 256K через env var

- **Дата:** 2026-05-07
- **PR:** `feature/global-max-tokens-256k`
- **ADR:** [docs/adr/0021-global-max-output-tokens.md](../adr/0021-global-max-output-tokens.md)
- **Связано:** ADR-0019 (stream для chat_async), PR #21 (точечный fix synthesize)

## Задача

В коде проекта были разбросаны 14 явных `max_tokens=N` (output cap для
LLM-вызовов): 2048, 4096, 8192, 16384, 65536. Все — upstream Ouroboros
artefact'ы времён GPT-3.5/4. На reasoning-style моделях (Kimi-k2p6 — наш
PRIMARY) эти лимиты обрезают видимый `content` потому что значительная
часть output уходит на скрытый `delta.reasoning_content` (CoT). PR #21
закрыл точечный случай для synthesize, Артём попросил убрать ограничения
везде разом.

## Решение (см. ADR-0021)

Один env var `OUROBOROS_MAX_OUTPUT_TOKENS=256000` (потолок Kimi).
`ouroboros.config.get_max_output_tokens()` — getter. `chat()` и `chat_async()`
с `max_tokens: Optional[int] = None`, на None — lookup из env. Все 14
hardcode-callsites переведены на «не передавать `max_tokens`» (default
из global config).

Coupled: stream-режим для синхронного `chat()` openai-compatible
(`_collect_stream_response_sync` helper) — без него 256K упёрся бы в
Hydra cap 4096 на non-stream путях.

## Файлы

**Conf + core:**

- `ouroboros/config.py` — env var, getter, env_keys
- `ouroboros/llm.py` — defaults Optional[int]=None, sync stream branch, helper
- `.env.example` — новая env var с комментарием

**Callsites (max_tokens=N удалён):**

- `ouroboros/agent_task_pipeline.py:382` (было 2048)
- `ouroboros/context_compaction.py:215` (было 16384)
- `ouroboros/tools/core.py:499` (было 4096)
- `ouroboros/tools/review.py:155` (было 65536)
- `ouroboros/tools/claude_advisory_review.py:451` (было 8192)
- `ouroboros/tools/review_synthesis.py:307` (было 2048)
- `ouroboros/tools/scope_review.py` 2 места (было _SCOPE_MAX_TOKENS)
- `ouroboros/tools/plan_review.py:392` (было _PLAN_REVIEW_MAX_TOKENS)
- `nefteboros/graphs/nodes/synthesize.py:135` (было 8192 после PR #21)
- `nefteboros/llm/gigachat.py:31` (4096 → Optional[int]=None)

**Не трогали:**

- `ouroboros/local_model.py` (probes, не output)
- `nefteboros/rag/chunker.py` (RAG-chunk size, не output)
- `ouroboros/consolidator.py` (мёртвый код)
- `ouroboros/llm.py vision_query` (не используется)

## Тесты

### AST

Все touched .py — валидны.

### Smoke

Probe Hydra прокси показал что 256K со stream проходит:

```
stream=true max_tokens=4096 → 200
stream=true max_tokens=16384 → 200
stream=true max_tokens=65536 → 200
stream=true max_tokens=131072 → 200
stream=true max_tokens=256000 → 200
```

256K — upper bound; модели реально не пишут столько в response, EOS
останавливает раньше. Деградации latency нет.

### Production verification

После deploy на сервер: UI «прогноз цен газа на год» → должны прийти числа
с CI 80/95% (вместо «пустой синтез»).

## Deployment notes

```bash
ssh -i ~/.ssh/id_ed25519_nefteboros root@186.246.2.190 "
  cd /root/nefteboros && \
  git pull --ff-only origin main && \
  echo 'OUROBOROS_MAX_OUTPUT_TOKENS=256000' >> .env && \
  systemctl restart nefteboros && \
  systemctl is-active nefteboros
"
```

Без env var на сервере — fallback на default 256000 из SETTINGS_DEFAULTS
(behavior идентичный). Явное добавление лучше неявного default.

## Связанные

- [ADR-0021](../adr/0021-global-max-output-tokens.md) — обоснование
- [ADR-0019](../adr/0019-system-prompt-analyst.md) — stream для chat_async
- PR #21 — точечный fix max_tokens для synthesize (этим PR глобализован)
