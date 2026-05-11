# 2026-05-11 — AITunnel LLM fallback (secondary provider)

## Задача

Prod в 07:25 UTC столкнулся с полной недоступностью LLM:

```
⚠️ All models are down. Primary (openai-compatible::kimi-k2p6)
and fallback (anthropic/claude-sonnet-4.6) both returned no response.
Last provider error: OpenAIError('Missing credentials.')
```

Root cause — внешний billing event на стороне Hydra-провайдера (под капотом Fireworks.ai):
```
HTTP 412: Account ... is suspended, possibly due to reaching the
monthly spending limit or failure to pay past invoices.
```

`OUROBOROS_MODEL_FALLBACK` указывал на `anthropic/claude-sonnet-4.6`, но `ANTHROPIC_API_KEY` на проде не настроен → fallback тоже падал. Демо невозможно.

## Что сделано

### Новый провайдер `aitunnel::` в `ouroboros/llm.py`

[AITunnel](https://api.aitunnel.ru/v1) — российский OpenAI-compatible прокси, поддерживает те же модели (kimi-k2.6, и др.). Используется как **secondary** при отказе Hydra.

Три точки изменения в `ouroboros/llm.py`:

1. **`_parse_provider_model`** — добавлен prefix `("aitunnel::", "aitunnel")` (между `openai-compatible::` и `openrouter::`).
2. **`_qualified_model_name`** — `aitunnel` → `aitunnel/<model>` для usage_model (cost tracking).
3. **`_resolve_remote_target`** — новый branch для `provider == "aitunnel"`:
   - `api_key` из env `AITUNNEL_API_KEY`
   - `base_url` из env `AITUNNEL_BASE_URL` (default `https://api.aitunnel.ru/v1`)
   - тот же streaming-режим что и openai-compatible (Hydra-proxy hack)

Strem-detection обновлён в двух местах:
```python
use_stream = target.get("provider") in ("openai-compatible", "aitunnel")
```

### Конфигурация на prod `.env` (НЕ в git — gitignored)

```bash
AITUNNEL_API_KEY=sk-aitunnel-...
AITUNNEL_BASE_URL=https://api.aitunnel.ru/v1
OUROBOROS_MODEL_FALLBACK=aitunnel::kimi-k2.6
```

`OUROBOROS_MODEL` (primary) остаётся `openai-compatible::kimi-k2p6` — Hydra первичный, aitunnel срабатывает только при empty response от primary (см. `ouroboros/loop.py:664` — auto-fallback logic).

## Smoke verification (2026-05-11 08:06 UTC, hot-patched container)

Probe «Какая сегодня погода в Москве?»:

```
t=8.0s   chunks[0] = 85 chars: "⚡ Fallback: openai-compatible::kimi-k2p6 → aitunnel::kimi-k2.6 after empty response"
t=18.6s  chunks[1] = 234 chars: "Вне моей компетенции. Я — аналитик нефтегазового рынка,
         отвечаю на вопросы по ценам на нефть и газ, ОПЕК+, санкциям, прогнозам Brent/WTI/Urals..."
```

Fallback chain отработала автоматически: Hydra suspended → empty response → notification → aitunnel kimi-k2.6 → корректный domain-aware refusal.

## Side-effect (положительный)

Observability gap для коротких refusals **временно закрылся**: при triggered fallback latency 20+s → `client.flush()` в `_patch_handle_task` успевает доставить trace в Langfuse до WS close. Подтверждено: probe создал `user_request` trace в Langfuse (chat:1, 234c, query «погода в Москве»). **Это совпадение**, не fix архитектурного gap — при восстановлении Hydra короткие refusals снова станут быстрыми и могут терять trace. Sync flush на server.py / `handle_task` wrap — отдельный PR.

## Что НЕ в PR

- `.env` на проде не коммитим (gitignored).
- Документирование AITUNNEL_* в `docs/deploy/production-config.md` — отложено в отдельный docs PR.
- Sync flush для observability коротких refusals — отдельный PR (см. side-effect).
- 3-уровневый fallback chain (Hydra → aitunnel → Anthropic) — отложен; сейчас 2-уровневый достаточен.

## Refs

- `ouroboros/loop.py:664-689` — auto-fallback logic (читает `OUROBOROS_MODEL_FALLBACK`).
- Reference: `reference_server_timeweb.md` (env переменные на проде) — обновится в memory.
