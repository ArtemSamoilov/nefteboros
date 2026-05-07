# Changelog: fix(synthesize) — max_tokens 2048 → 8192 для reasoning-style моделей

- **Дата:** 2026-05-07
- **PR:** `feature/fix-synthesize-max-tokens`
- **Связанные:** ADR-0014 (analyst graph), ADR-0019 (system-prompt + stream-fix в chat_async),
  PR #20 (numpy.bool fix)

## Задача

После PR #20 forecast pipeline перестал падать с PydanticSerializationError,
но в production всё равно возвращался **пустой synthesis**. Agent в UI
сказал «forecast возвращает пустой ответ — внутренний сбой генерации синтеза»
вместо числового прогноза с CI.

В task_results на сервере видно: agent сделал 4 tool-call'а (3× `analyst_query`
с разными формулировками + 1× `rag_search`), все analyst_query вернули
`synthesis: ""`. Не error — просто пустая строка.

## Корневая причина

`nefteboros/graphs/nodes/synthesize.py:135` явно ставит `max_tokens=2048`
для LLM-вызова синтеза. PRIMARY_LLM_MODEL у нас `kimi-k2p6` —
**reasoning-style** модель Hydra. Через стрим (PR #18 `chat_async` fix)
Kimi возвращает два канала:

- `delta.content` — финальный текст ответа (его аккумулирует наш
  `_collect_stream_response`)
- `delta.reasoning_content` — vendor extension, скрытый chain-of-thought

При `max_tokens=2048` Kimi сжигает большую часть бюджета на reasoning
(CoT через `reasoning_content`), на видимый `content` остаётся 100-200
токенов. Это обрезает synthesis в середине таблицы:

```
content len: 174
content[:300]: 'Прогноз спотовых цен TTF и Henry Hub на 12 месяцев (точка: 7 мая 2027 г.).\n\n| Актив | Базовая оценка | CI 80% | CI 95% | Метод |\n|---|---|---|---|---|\n| TTF | 35.40 EUR/MWh |'
usage: prompt_tokens=2123, completion_tokens=4096
```

`completion_tokens=4096` — модель **исчерпала** запрошенный бюджет (1024
output фактически = из-за reasoning_content). Agent видит эти 174 chars,
интерпретирует как «пустой / некорректный синтез» и отвечает creator'у
честно «forecast вернул пустой ответ, не выдумываю цифры».

Промпт PR #18 при этом отработал ровно как задумано — anti-hallucination
не дала сфабриковать прогноз. Но первичный сценарий ТЗ §2.5 не работает.

## Решение

`max_tokens=2048` → `max_tokens=8192` в одной строке + поясняющий
комментарий о ловушке reasoning-моделей.

Stream-режим для `openai-compatible` (PR #18 в `ouroboros/llm.py:chat_async`)
уже разблокирует cap прокси Hydra на 4096, так что 8192 проходит через тот
же путь без изменений.

Проверка на том же production-grade prompt'е (system_analyst.md +
synthesize_forecast_only.md):

| max_tokens | content len | completion_tokens | result |
|---|---|---|---|
| 2048 (was) | 174 | 4096 (capped, truncated) | broken — agent видит "пустой" |
| 4096 | 174 | 4096 (capped) | broken — то же |
| **8192 (new)** | **1229** | **4297** | **полный analytical synthesis с CI** ✅ |

## Файлы

**Изменено:**

- `nefteboros/graphs/nodes/synthesize.py:135` — одна строка + комментарий

**Добавлено:**

- `docs/changelog/2026-05-07-fix-synthesize-max-tokens.md` (этот файл)

ADR отдельный не пишу — bugfix совместимости с reasoning моделями,
не архитектурное решение.

## Что НЕ в PR

- **`derived_layer.py` synthesize call** — не существует, derived_layer не
  делает LLM calls.
- **Адаптация `max_tokens` per-model** — overkill для одной известной
  ситуации. Если в будущем переключимся на non-reasoning LLM (DeepSeek-v3p1,
  GPT-OSS-120B), 8192 безопасный upper bound.
- **`reasoning_effort=low`** через `extra_body` — для Kimi-k2p6 этот
  параметр через Hydra **игнорируется** (мы это видели в diag PR #18 —
  completion_tokens константный 21/77/80/80/80 для none/low/medium/high/max).
  Не помогает.
- **Подавление `reasoning_content`** через output filter — vendor extension,
  не в стандартной spec OpenAI. `_collect_stream_response` его и так
  игнорирует. Просто бюджет на content нужно увеличить.

## Тесты

### AST

`nefteboros/graphs/nodes/synthesize.py` — валиден.

### Repro + verify

Прямой вызов `LLMClient.chat_async` с production-grade synthesize prompt'ом:

- До: 174 chars (truncated mid-table)
- После: 1229 chars (полный синтез с TTF + Henry Hub + CI 80/95%)

После deploy на сервер первый forecast-запрос («прогноз цен газа на год»)
должен вернуть числовую таблицу с прогнозами.

## Deployment notes

```bash
ssh -i ~/.ssh/id_ed25519_nefteboros root@186.246.2.190 "
  cd /root/nefteboros && \
  git pull --ff-only origin main && \
  systemctl restart nefteboros && \
  systemctl is-active nefteboros
"
```

Никаких pip / migrations / env vars.

## Связанные

- ADR-0014 (`docs/adr/0014-langgraph-subgraph.md`) — synthesize node в графе
- ADR-0019 (`docs/adr/0019-system-prompt-analyst.md`) §«Anti-hallucination» —
  где промпт корректно отработал на этой ситуации (агент честно сказал
  «пустой ответ» вместо галлюцинации)
- PR #18 — stream-режим в `chat_async` для openai-compatible (без него
  даже max_tokens=8192 не пройдёт через Hydra cap 4096)
- PR #20 — fix numpy.bool в forecast metadata (предыдущий слой одного и
  того же сценария)
