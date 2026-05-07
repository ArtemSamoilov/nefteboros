# ADR-0021 — Глобальный `max_tokens` 256K через env var

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/global-max-tokens-256k`
- **Связано:** ADR-0019 (system-prompt + stream для chat_async),
  PR #21 (synthesize max_tokens 2048 → 8192 — точечный fix этой же проблемы)

## Контекст и проблема

В коде проекта (`ouroboros/` + `nefteboros/`) разбросаны 14 явных значений
`max_tokens=N` (output cap для LLM-вызовов): 2048, 4096, 8192, 16384, 65536.
Все — artefact'ы upstream Ouroboros времён GPT-3.5/4, когда `4096` был типовым
безопасным дефолтом. Эти числа **не соответствуют реальности 2025-2026**:

- **Kimi-k2p6** (наш PRIMARY) — 256K context window, но как **reasoning model**
  большую долю output уходит на скрытый `delta.reasoning_content` (CoT).
  При `max_tokens=2048` видимый `content` обрезается до неинформативного куска.
  Прецедент — PR #21 (synthesize): 2048 → пустой ответ → agent говорит
  «forecast вернул пустой синтез» → demo сломан.
- **DeepSeek-v3p1, MiniMax-m2p7, GPT-OSS-120B** — non-reasoning, дают полный
  ответ в <2K токенов; для них старые ограничения не блокируют, но и пользы
  не несут.

Артём после идентификации root cause в PR #21 поднял вопрос: **уберём
ограничения везде разом, сразу на потолок Кими (256K)**. Точно не упрёмся.

## Решение

Ввести **одну глобальную env var** `OUROBOROS_MAX_OUTPUT_TOKENS=256000`
с getter'ом `ouroboros.config.get_max_output_tokens()`. Все 14 callsites
заменены на «не передавать `max_tokens`» — подхватывается global default.

`OUROBOROS_MAX_OUTPUT_TOKENS` добавлен в `SETTINGS_DEFAULTS` (значение 256_000)
и в `apply_settings_to_env` env_keys — то есть конфигурируется и через env,
и через `settings.json`, и через UI Settings page (если поле явно туда
прокинуть в будущем).

`chat()` и `chat_async()` в `ouroboros/llm.py` теперь имеют
`max_tokens: Optional[int] = None` — на None lookup'ит default из getter'а.

## Stream для синхронного `chat()` (coupled fix)

Проблема: Hydra-прокси отвергает non-stream запросы с `max_tokens > 4096`
(error 400 `«Requests with max_tokens > 4096 must have stream=true»`).
ADR-0019 включил stream только для `chat_async`. Для `chat()` (sync, используется
в `agent_task_pipeline`, `context_compaction`, `tools/core`,
`tools/claude_advisory_review`, `tools/review_synthesis`) stream **не был**
включён → 256K через эти пути упирался в Hydra cap.

Fix: новый helper `LLMClient._collect_stream_response_sync(client, kwargs)`
(зеркало async-версии: накапливает `delta.content`, читает usage из последнего
chunk через `stream_options={"include_usage": True}`, упаковывает в формат
non-stream `model_dump()`). В `_chat_remote` для openai-compatible auto-включается
как и в `_chat_async` — провайдеры Anthropic/OpenAI direct/OpenRouter работают
через свой не-stream путь без изменений.

## Файлы — что изменилось

**Conf и core:**

- `ouroboros/config.py`:
  - `SETTINGS_DEFAULTS["OUROBOROS_MAX_OUTPUT_TOKENS"] = 256_000`
  - функция `get_max_output_tokens() -> int`
  - `apply_settings_to_env` — добавлен ключ в `env_keys`
- `ouroboros/llm.py`:
  - `chat(max_tokens: Optional[int] = None)` + lookup
  - `chat_async(max_tokens: Optional[int] = None)` + lookup
  - `_chat_remote` — добавлен `use_stream` branch для openai-compatible
  - новый `_collect_stream_response_sync` (sync mirror async-версии)
- `.env.example` — `OUROBOROS_MAX_OUTPUT_TOKENS=256000` + объяснение

**Callsites (8 удалений `max_tokens=N`):**

- `ouroboros/agent_task_pipeline.py:382` — было 2048
- `ouroboros/context_compaction.py:215` — было 16384
- `ouroboros/tools/core.py:499` — было 4096
- `ouroboros/tools/review.py:155` — было 65536
- `ouroboros/tools/claude_advisory_review.py:451` — было 8192
- `ouroboros/tools/review_synthesis.py:307` — было 2048
- `ouroboros/tools/scope_review.py` (2 места) — было `_SCOPE_MAX_TOKENS`
- `ouroboros/tools/plan_review.py:392` — было `_PLAN_REVIEW_MAX_TOKENS`

**Nefteboros:**

- `nefteboros/graphs/nodes/synthesize.py:135` — было 8192 (после PR #21);
  теперь default 256K
- `nefteboros/llm/gigachat.py:31` — параметр default `4096` → `Optional[int] = None`;
  пустое значение пробрасывается в `langchain_gigachat`, который использует
  свой default (GigaChat имеет собственный output cap ~16K — не 256K)

**Не трогали (отдельная семантика):**

- `ouroboros/local_model.py:760, 787` (`max_tokens=32, 256`) — короткие probes
  для local model availability check, **не output**. Семантически другое.
- `nefteboros/rag/chunker.py` (`max_tokens=4000`) — целевой размер RAG-чанка
  при индексации, **не output**. Concept не пересекается.
- `ouroboros/consolidator.py` (4 места × 4096) — мёртвый код в нашем форке
  (выпилен в ADR-0001), не вызывается; для чистоты diff не трогаем.

## Аргументация ключевых решений

### Почему 256_000 (а не 32K / 64K / 131K)

Артём сформулировал: «ставим под потолок Кими, точно не упрёмся». 256K =
context window Kimi-k2p6. Реально модель не выдаст 256K в response (нет
training pressure писать столько); upper bound безвреден. Меньшие значения
(32K) кажутся «безопасными», но на reasoning-style моделях с ростом
prompt'а легко могут не хватить (как было 8192 в synthesize.py — хватало
до того как мы прочитали SYSTEM.md ещё длиннее).

### Почему через env var, а не hardcode

- Проще override per-deployment: production хочет 64K (сэкономить cost),
  staging 256K (для debug длинных responses).
- Через `settings.json` + UI Settings — owner может крутить runtime-конфиг.
- Если завтра переезжаем на провайдера с другим cap'ом (Anthropic 16K,
  GigaChat 16K) — меняется одно место.

### Почему None default + lookup, а не factory default

`max_tokens: int = get_max_output_tokens()` (вычисление при импорте) — плохо:
env var может быть установлена позже (например в `apply_settings_to_env`
после load_settings), значение замёрзнет на момент импорта модуля.

`max_tokens: Optional[int] = None` + lookup в теле — корректно: при каждом
вызове свежий read из env. Цена — одна `int(os.environ.get(...))` per call,
ничтожно.

### Почему удаляем `max_tokens=N` совсем, а не заменяем на `max_tokens=get_max_output_tokens()`

DRY и читаемость. Если в callsite **нет** аргумента — читатель видит
«использует default». Если стоит `max_tokens=get_max_output_tokens()` —
тот же default но с лишним boilerplate.

Минус: если в будущем в этом конкретном callsite понадобится override
(например review-pipeline хочет ровно 32K чтобы экономить cost) — нужно
будет добавить параметр заново. Это не критично, callsites наперечёт.

### Почему НЕ трогаем `consolidator.py`

Файл полностью выпилен из run-time графа в ADR-0001 (часть self-modify
machinery). 4 callsites `max_tokens=4096` там — мёртвый код, не выполняются.
Заменять — расширять scope PR без пользы.

## Что НЕ в PR

- **Удаление `consolidator.py` целиком** — отдельный PR (ADR-0001 follow-up).
- **Per-provider clamping** — Anthropic/GigaChat имеют меньший hard cap.
  Сейчас при попытке 256K к ним они вернут 400 — fail-fast, owner поправит.
  Автоматический clamp = silently truncate behavior — нежелательно.
- **`vision_query.max_tokens=4096`** (`llm.py:1687`) — vision endpoint в
  нашем проекте не используется, не trogаli ради scope.
- **Тесты на `get_max_output_tokens()`** — оставлены за scope,
  тестируется через end-to-end (production smoke).

## Альтернативы рассмотренные

- **Tупо поднять все hardcode 4096 → 256000 без env var**: проще, но теряем
  гибкость override без правки кода.
- **Snести параметр `max_tokens` совсем** — пусть провайдер решает: рискованно
  для providers с runaway tendencies (модель пишет бесконечно). 256K upper
  bound — defensive.
- **Per-call `max_tokens` остаётся, но default 256K**: то что и сделано.
- **Per-provider config**: `OUROBOROS_MAX_TOKENS_OPENAI_COMPATIBLE=256000`,
  `OUROBOROS_MAX_TOKENS_ANTHROPIC=16384` — overengineering для текущего
  состояния. Когда мульти-провайдер реально станет проблемой — добавим.

## Последствия

**Плюсы:**

- Закрыт класс багов «artificial max_tokens обрезает reasoning-output»
  (PR #21 был последним прецедентом).
- Один env var — один источник правды, легко настраивать на deployment.
- Никаких magic-numbers по проекту в LLM-вызовах.
- Stream для sync `chat()` разблокирует ещё несколько code paths которые
  раньше упирались в Hydra cap (advisory review, review_synthesis,
  agent_task_pipeline summary, etc).

**Минусы / риски:**

- Anthropic/GigaChat при `max_tokens=256000` вернут 400 — для них нужен
  override. На текущем production это не задействовано (PRIMARY = Hydra).
- Stream для sync `chat()` — изменение пути выполнения для openai-compatible.
  Может выявить latent bugs в normalize_remote_response при stream-shaped
  payload (хотя я аккуратно мимикрирую non-stream формат).

**Митигации:**

- Smoke-тестирование на production deploy.
- Если Anthropic/GigaChat понадобится — добавим override в конкретные
  callsites (review.py может захотеть 16K на Anthropic).

## Тестирование

### AST

`ouroboros/config.py`, `ouroboros/llm.py`, и все touched callsite-файлы.

### Production smoke

После deploy:
1. `/api/health` → 200
2. `/api/extensions/neftegaz_analyst/health` → tools=[analyst_query, rag_search]
3. UI «прогноз цен газа на год» → forecast pipeline отдаёт числа с CI

## Ссылки

- ADR-0019 — stream support для chat_async (только async, теперь добавили sync)
- PR #21 — точечный fix max_tokens для synthesize
- `ouroboros/config.py` — `OUROBOROS_MAX_OUTPUT_TOKENS` + `get_max_output_tokens()`
- `ouroboros/llm.py` — chat()/chat_async() + `_collect_stream_response_sync`
