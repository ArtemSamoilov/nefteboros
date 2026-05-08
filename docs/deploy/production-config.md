# Production config — required state for nefteboros server

- **Сервер:** Timeweb VDS, IP `186.246.2.190`
- **Скрипт применения:** [`scripts/deploy/apply_production_config.py`](../../scripts/deploy/apply_production_config.py)
- **Связанные:** ADR-0001, ADR-0019, ADR-0021

## Проблема

`config.py:SETTINGS_DEFAULTS` имеет default `OUROBOROS_MODEL=anthropic/claude-opus-4.7`, `OUROBOROS_MODEL_FALLBACK=anthropic/claude-sonnet-4.6`. Если `settings.json` на сервере не содержит явных override'ов — main loop пытается вызывать Anthropic, для которого `ANTHROPIC_API_KEY` **не настроен** → fallback chain: «All models are down».

Это **не выявляется** при testing analyst graph (там собственная конфигурация через `.env` `PRIMARY_LLM_*`), но **ломает Ouroboros main loop** при tool-selection chat'ах.

## Желаемое состояние production config

### `data/ouroboros/settings.json`

| Key | Value |
|---|---|
| `OPENAI_COMPATIBLE_BASE_URL` | `https://hydragpt.ru/v1` |
| `OPENAI_COMPATIBLE_API_KEY` | `hydra_…` (из `.env` `HYDRA_API_KEY`) |
| `OUROBOROS_MODEL` | `openai-compatible::kimi-k2p6` |
| `OUROBOROS_MODEL_CODE` | `openai-compatible::kimi-k2p6` |
| `OUROBOROS_MODEL_LIGHT` | `openai-compatible::kimi-k2p6` |
| `OUROBOROS_MODEL_FALLBACK` | `openai-compatible::kimi-k2p6` |
| `OUROBOROS_AUTO_ENABLE_SKILLS` | `neftegaz_analyst:analyst_query,neftegaz_analyst:rag_search` |
| `OUROBOROS_REVIEW_MODELS` | `deepseek-v4-pro,minimax-m2p7,gpt-oss-120b` (все openai-compatible) |
| `OUROBOROS_SCOPE_REVIEW_MODEL` | `openai-compatible::deepseek-v4-pro` |
| `OUROBOROS_RUNTIME_MODE` | `advanced` |
| `OUROBOROS_MAX_OUTPUT_TOKENS` | `256000` |
| `OUROBOROS_NETWORK_PASSWORD` | (preserved as-is) |

### `/root/nefteboros/.env`

| Key | Value | Назначение |
|---|---|---|
| `PRIMARY_LLM_PROVIDER` | `hydra` | analyst graph synthesize |
| `PRIMARY_LLM_MODEL` | `kimi-k2p6` | |
| `ROUTING_LLM_PROVIDER` | `gigachat` | intent classify, llm_disambiguate |
| `ROUTING_LLM_MODEL` | `GigaChat-2-Max` | |
| `OUROBOROS_MAX_OUTPUT_TOKENS` | `256000` | глобальный output cap (ADR-0021) |
| `OUROBOROS_SERVER_HOST` | `0.0.0.0` | public bind для UI на 8765 |
| `HYDRA_API_KEY` | (preserved) | api ключ Hydra |
| `GIGACHAT_*` | (preserved) | GigaChat credentials |
| `EIA_API_KEY` | (preserved) | forecast SARIMAX exogenous |

## Apply procedure

С dev-машины:

```bash
# 1. Push скрипта на сервер
scp -i ~/.ssh/id_ed25519_nefteboros \
  scripts/deploy/apply_production_config.py \
  root@186.246.2.190:/root/nefteboros/scripts/deploy/apply_production_config.py

# 2. Run на сервере (HYDRA_API_KEY уже в /root/nefteboros/.env)
ssh -i ~/.ssh/id_ed25519_nefteboros root@186.246.2.190 \
  "cd /root/nefteboros && \
   source .env && \
   chmod +x scripts/deploy/apply_production_config.py && \
   python3 scripts/deploy/apply_production_config.py && \
   systemctl restart nefteboros && \
   sleep 4 && \
   systemctl is-active nefteboros"
```

Скрипт:
- **Идемпотентен** — можно запускать многократно, existing `OUROBOROS_NETWORK_PASSWORD`, `HYDRA_API_KEY`, `GIGACHAT_*` keys не перезаписываются
- Создаёт `.bak.<timestamp>` копии перед изменениями
- Не трогает: `EIA_API_KEY`, `BRAVE_API_KEY`, `TELEGRAM_*` и пр.

## Verification после apply

```bash
ssh -i ~/.ssh/id_ed25519_nefteboros root@186.246.2.190 \
  "grep -E '^OUROBOROS_MODEL|^PRIMARY_LLM' /root/nefteboros/.env && \
   python3 -c 'import json; d = json.load(open(\"/root/nefteboros/data/ouroboros/settings.json\")); print(json.dumps({k: v for k, v in d.items() if k.startswith(\"OUROBOROS_MODEL\") or k.startswith(\"OPENAI_COMPATIBLE\")}, indent=2))'"
```

## Rollback

`.bak.<timestamp>` копии создаются автоматически. Откат:

```bash
ssh -i ~/.ssh/id_ed25519_nefteboros root@186.246.2.190 \
  "cp /root/nefteboros/data/ouroboros/settings.json.bak.<TS> /root/nefteboros/data/ouroboros/settings.json && \
   cp /root/nefteboros/.env.bak.<TS> /root/nefteboros/.env && \
   systemctl restart nefteboros"
```

## Когда запускать

- **После каждого первого деплоя на новый сервер**
- **После любого случая когда `settings.json` затирается**
- **При смене HYDRA_API_KEY** — обновить `.env` руками, затем скрипт (он не перезаписывает existing API key в settings.json)
