# 2026-05-24 — Observability & eval robustness (предпосылка пересчёта E2E)

## Задача

Сделать observability и длинный eval-прогон надёжными — предпосылка для
осмысленного пересчёта E2E. Три приоритета: (1) диагностировать и устранить
tail-timeout leak испорченного 100-прогона v2.3.5 (43/100 timeout в хвосте),
(2) cost/token/model enrichment на generation observations, (3) trace-loss
flush race.

Ветка `fix/observability-eval-robustness`, Python 3.12.

## Диагностика tail-timeout (BLOCKER №1) — root cause НЕ в observability

Профилировка по логам самого испорченного прогона (`~/Ouroboros/data/logs/`,
сохранились) + микробенч Langfuse. Четыре линии доказательств:

1. **Монотонная деградация, не сбой.** В `metrics/runs/...c3c22f6.json`:
   57 ok / 43 timeout, **ноль crash/connection-refused** в server.log. Время на
   10-диалоговый батч: 24→27→42→61→112→157 мин. Хвост ~7× медленнее головы.
2. **Бьёт по всем сценариям, включая безынструментальные** (`out_of_scope`,
   `adversarial` refusal'ы тоже в timeout) → деградация глобальная, не
   tool-specific. В server.log ноль `greenlet`/`chromium`/`Too many open files`/
   `Traceback`/`MemoryError` → не browser, не FD/thread-leak.
3. **Контекст растёт до cap.** `task_done` события: 1-round задачи несут
   `prompt_tokens` 131k→174k по ходу прогона, упор в `soft_cap=200_000`.
   Multi-round задачи хвоста суммируют 1.3–1.95M токенов; одна — 3776s (63 мин).
   После 21:01 UTC — ноль завершённых задач за оставшиеся 7ч.
4. **Источник.** Все 100 WS-диалогов → один серверный чат (`chat_id=1`),
   `ouroboros/context.py::build_recent_sections` инжектит `## Recent chat` из
   **глобального** `chat.jsonl` (хвост 1000, без фильтра по диалогу) в каждую
   задачу. История копится → контекст пухнет.

**Observability оправдана:** без ключей (условие прогона) langfuse 4.6.1
самоотключается (`auth_check=False`). Микробенч 600 циклов
`start_as_current_observation`+`flush`: **RSS +0.0MB, threads=1**. Disabled-
клиент инертен, leak'а не создаёт.

**Вывод:** это дефект eval-харнесса (диалоги контаминируют друг друга через
растущую историю), не observability-leak. Baseline v2.3.5 частично невалиден —
хвост умирал от context-bloat'а, не от качества агента.

## Что сделано

### 1. Изоляция диалогов в WSRunner (фикс BLOCKER №1) — код

`scripts/eval/eval_e2e.py`: `WSRunner._clear_chat_history()` дёргает
`POST /api/chat/clear` (существующий endpoint, ADR-0020) **перед каждым
диалогом**. Убирает аккумулятор `Recent chat` и не даёт консолидатору достичь
порога 100 сообщений. Escape hatch `EVAL_CHAT_ISOLATION=0` (diagnostics /
будущий genuine multi-turn eval). Без новых зависимостей (stdlib urllib через
`asyncio.to_thread`). Архитектурное решение — ADR-0027.

### 2. Cost/token JSONL fallback (scope #2) — код

Проверено: `LLMClient.chat[_async]` возвращает `Tuple[msg, usage]` — **usage из
ouroboros приходит**; `_extract_usage_kwargs`+`compute_cost` корректно подключены
к Langfuse generation span (`_patch_llm_client_chat`). `compute_cost` для
kimi-k2p6 даёт корректный cost (напр. 24500p+237c → $0.0115), тогда как
Ouroboros-pricing возвращает 0.0 (не знает наши модели).

Gap: без Langfuse-ключей этот cost шёл только в disabled-клиент → терялся.
Фикс: `_patch_llm_client_chat` теперь дублирует usage в nefteboros JSONL-tracer
через `log_llm_usage()` (guard'нуто, чтобы ошибка tracer'а не пере-вызвала LLM).
Cost/tokens/model становятся видны в `trace.jsonl` offline без ключей.

### 3. Trace-loss flush race (scope #3) — анализ, без изменения кода

Актуально **только при наличии Langfuse-ключей** (без них трейсов нет вовсе —
JSONL tracer покрывает offline). `force_flush` уже откачен ранее (ломал
child-trace propagation) — не повторяю.

**Actionable рецепт (вместо правки кода):** langfuse 4.x `get_client()` читает
конфиг батча из env — `LANGFUSE_FLUSH_AT` (порог числа событий на flush, дефолт
15) и `LANGFUSE_FLUSH_INTERVAL`. Это и есть ось «BatchSpanProcessor vs
SimpleSpanProcessor»: `LANGFUSE_FLUSH_AT=1` ≈ Simple (export на каждое событие,
дороже по сети, минимальное окно race), дефолт ≈ batch. Для eval-прогона с
ключами выставить `LANGFUSE_FLUSH_AT=1` через env — **без правки кода и без
риска повторить откаченный force_flush**. Не верифицирую сейчас (ключей нет);
бэклог v2.4 — проверить valid-trace-ratio с ключами под этим knob'ом.

## Верификация

Окружение: Python 3.12 (`.venv312`), server.py на :8000, prod-config
(kimi-k2p6 + GigaChat routing), RAG vectorstore_v2_heading (802 docs),
`LANGFUSE_ENABLED` дефолт true без ключей (= условие испорченного прогона).

### Leak-фикс — детерминированный proof механизма (реальный код + реальные данные)

`build_llm_messages` на реальном `chat.jsonl` испорченного прогона (422 записи),
замер `cap_info.estimated_tokens`:

| chat-записей | контекст (токены) | vs пусто |
|---|---|---|
| 0 (после clear) | 18 426 | base |
| 25 | 24 453 | +6 027 |
| 50 | 34 448 | +16 022 |
| 100 | 54 928 | +36 502 |
| 200 | 84 509 | +66 083 |
| 422 | 141 608 | +123 182 |

~600 токенов на запись истории. Контекст растёт монотонно с числом прошлых
диалогов → к 100-му упор в `soft_cap`. **Очистка (n=0) → контекст возвращается к
18k базе** — leak устранён.

### Leak-фикс — реальный smoke (изоляция ON, server.py + RAG)

Прогон `eval_e2e --ws --limit 5` с фиксом, метрика `round=1 prompt_tokens`
каждого диалога (events.jsonl) — чистый сигнал базового контекста (до tool-output):

| диалог | scenario | round1 prompt_tokens |
|---|---|---|
| 1 | rag_only | 24 502 |
| 2 | web_only | 24 514 |
| 3 | rag_plus_web | 24 553 |
| 4 | forecast | 24 705 |

Δ = **+203 токена за 4 диалога (+68/диалог)** — плоско (вариативность контента
текущего диалога), против роста 131k→174k (+1.3k/диалог, до cap) в испорченном
прогоне. `chat.jsonl` после прогона содержит **только последний диалог** (очистка
перед каждым truncate'ит — иначе было бы ~10 записей), история не копится. Все
диалоги завершаются (`task_done` присутствует) — **трейсы не теряются**.

### Observability inert без ключей

Микробенч 600 циклов `start_as_current_observation`+`flush` с keyless-клиентом:
RSS +0.0MB, threads=1. Подтверждает, что observability не источник leak'а.

### Cost/tokens

`compute_cost("kimi-k2p6", 24500, 237)` → **$0.0115** (корректно), тогда как
Ouroboros-pricing для той же пары → **$0.0** (не знает модель). Wiring
usage→span (`_extract_usage_kwargs`+`span.update`) для Langfuse-generation
проверен. `trace.jsonl` пишется (traced_tool спаны rag_search) — JSONL-трейсы не
теряются. **Честная оговорка:** main-loop `ouroboros_chat` LLM-вызовы идут вне
nefteboros-спанов, поэтому их cost в `trace.jsonl` не попадает (см. «Не сделано»);
authoritative offline-источник токенов в WS-пути — Ouroboros `events.jsonl`
(`llm_usage`/`task_done`), к которым применим nefteboros `compute_cost`.

## Что НЕ сделано (явно)

- **Trace-loss flush race не реализован** — не верифицируем без Langfuse-ключей,
  риск повторить откаченный force_flush. Бэклог v2.4.
- **Orphan tool traces из background tasks** — v2.4 (как в предыдущем changelog).
- **Рост контекста в реальной prod-сессии** — та же причина, но не блокирует
  пересчёт E2E; отдельная задача (бэклог).
- **Базовые ~24–130k токенов на analyst-запрос** (SYSTEM.md+BIBLE+ARCHITECTURE+…
  + накопленная Drive-память) — наследие Ouroboros, оптимизация контекста вне
  scope. Фикс даёт надёжность прогона, не скорость (~3.5–4ч на чистые 100).
- **JSONL tracer в no-skill пути** — cost попадает в `_current_span` только когда
  активен nefteboros span (analyst-skill через `traced_tool`); чистый
  refusal без скилла трейса не пишет. Полное покрытие WS-пути — отдельно.
- Сам пересчёт E2E — отдельный воркер (по заданию).

## Файлы

- Изменено: `scripts/eval/eval_e2e.py` (изоляция диалогов + env-gate),
  `nefteboros/observability/_ouroboros_patches.py` (log_llm_usage JSONL fallback).
- Добавлено: `docs/adr/0027-eval-dialogue-isolation.md`, этот changelog.

## Связанные

- ADR-0027 (eval-dialogue-isolation), ADR-0024 (observability-langfuse),
  ADR-0020 (chat-clear-button).
- changelog 2026-05-11-eval-rebaseline-v2.3.5 (источник испорченного прогона),
  2026-05-11-observability-post-span-flush (flush race, force_flush revert).
