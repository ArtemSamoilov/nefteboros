# ADR-0020 — Кнопка «New chat» и `POST /api/chat/clear`

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/chat-clear-button`
- **Связано:** ADR-0001 (форк Ouroboros — какие подсистемы выпилены),
  ADR-0019 (system-prompt-analyst — там подняли вопрос длинного thread'а)

## Контекст и проблема

В Ouroboros концептуально один непрерывный диалог с агентом (upstream design
«один agent → один dialog»). Истории `data/ouroboros/logs/chat.jsonl` +
`logs/progress.jsonl` накапливаются без явного механизма «начать новый чат».
В upstream длинные диалоги сжимались `consolidator.py`, но в нашем форке этот
модуль выпилен (см. ADR-0001), поэтому компактификации старого dialog'а нет —
context просто растёт линейно с каждым сообщением.

После demo-thread'а из 50-80 обменов context упрётся в cap LLM API
(Kimi-k2p6 = 256K). Soft cap в `apply_message_token_soft_cap` fail-open
(флагирует, но не truncate'ит — это policy «No silent truncation» из upstream
BIBLE.md).

Артём после первого взгляда на UI задал ровно правильный вопрос: «как это
работает, если контекст не вечный?» — и попросил добавить кнопку «новый чат».
Минимальное удобство для demo и эксплуатации.

`POST /api/reset` уже существует, но **не подходит** — он удаляет всё runtime
(`state/`, `memory/`, `logs/`, `archive/`, `locks/`, `task_results/`,
`uploads/`, `settings.json`). После него теряется skill enable, network
password, identity, scratchpad. Это полный factory reset, не «новый чат».

## Решение

Новый endpoint `POST /api/chat/clear` + кнопка `New chat` в `chat.js` header
(между `Review` и `Restart`).

### Backend (`ouroboros/server_history_api.py`)

`make_chat_clear_endpoint(data_dir)` возвращает Starlette handler. Truncate
ровно двух файлов которые UI читает через `/api/chat/history`:

- `logs/chat.jsonl` — основные user/assistant/system сообщения
- `logs/progress.jsonl` — progress notes (тоже видны в chat как `is_progress`)

**НЕ трогает:**

- `settings.json` — network password, provider config, runtime mode
- `memory/*` — `identity.md`, `scratchpad.md`, `knowledge/`, `dialogue_blocks.json`
- `state/*` — skill enable, advisory review, queue snapshot
- `logs/events.jsonl`, `logs/tools.jsonl`, `logs/supervisor.jsonl` — технические
  трассы (постмортем нужен) которые НЕ показываются в chat UI

Truncate через `path.write_text("")`. Race с writer возможна (одна сторонняя
строка может остаться) — UI после fetch делает `syncHistory()`, ребуилдит из
файла как есть. Не критично.

### UI (`web/modules/chat.js`)

Кнопка `<button data-chat-command="new-chat">New chat</button>` в
`chat-header-actions`. Handler в существующем listener'е:

1. `confirm()` диалог с пояснением что **не** очищается (settings/память/skill).
2. `fetch('/api/chat/clear', {method: 'POST'})` → ожидаем 200.
3. Очистка DOM: `messagesDiv.innerHTML = ''` + восстановление `typingEl`.
4. `syncHistory({fromReconnect: true})` — перетягивает пустую history с сервера,
   сбрасывает `retiredTaskIds`, ставит `historyLoaded = true`.
5. На ошибке — `alert()` с описанием.

Кнопка не toggle (нет `on`/`off` state) — `syncHeaderControlState` её
игнорирует, отдельная обработка не нужна.

## Аргументация ключевых решений

### Почему REST endpoint, не WebSocket `/clear` команда

Pattern WebSocket-команд (`/restart`, `/panic`, `/evolve`, `/bg`) в
`server.py:process_bridge_updates` обрабатывает **владельческие сообщения**.
`/clear` концептуально нарушает порядок: команда `/clear` сама бы записалась
в `chat.jsonl` ПЕРЕД truncate'ом, и после очистки в логе осталась бы **она
одна** — путаница.

REST endpoint — отдельный канал, чистый, идемпотентный, проще тестируется
через curl, не нагружает supervisor message bus.

### Почему `New chat`, а не `Clear`

«Clear» подразумевает destructive ровно историю — пользователь может бояться
что снёс identity / scratchpad / settings. «New chat» — позитивный фрейминг
(«начнём с чистого листа»), тот же UX-паттерн что в ChatGPT. Confirm диалог
явно перечисляет что **не** затрагивается — это ключевая защита от страха
утратить настройки.

### Почему НЕ удаляем events.jsonl / tools.jsonl

Эти файлы — execution traces, не chat history. Они нужны для:

- Постмортема при ошибках tool'а («что именно вернул retriever на запрос X»)
- Cost-аналитики (`/api/cost-breakdown` агрегирует `events.jsonl`)
- Diagnostic мне (или Артёму) при поддержке

Удаляться они должны по ротационной политике (logrotate), не по «новый чат»
кнопке.

### Почему НЕ truncate `dialogue_blocks.json` / `scratchpad.md`

`dialogue_blocks.json` — output старого consolidator'а (в нашем форке не
работает после ADR-0001, файл может быть пустым или legacy). `scratchpad.md`
— рабочая память агента. И то, и другое — **извлечённые знания**, не
транскрипт. Удалять при «новый чат» концептуально неправильно: пользователь
ожидает что агент **помнит** свою роль и накопленный context, просто старые
тексты сообщений уйдут.

Если в будущем decide вернуть consolidator — `dialogue_blocks.json` станет
важным persistence layer; trash его с каждой кнопкой = потеря инвестиции.

## Что НЕ в этом PR

- **CSS-стили для `.danger` варианта** — кнопка обычная `chat-header-btn`. У
  Artём не было запроса на акцент.
- **Confirmation в виде modal вместо нативного `confirm()`** — нативный
  достаточен, единственный confirm в проекте (`/panic` тоже использует
  `confirm()`).
- **Auto-truncate политика** (например, при превышении N сообщений) —
  отдельная задача, требует решения о seman tics. См. ADR-0019 §«Что
  потерялось при выпиле в нашем форке».
- **Возврат consolidator'а** — отдельный архитектурный PR, не этот.
- **Ratelimit на endpoint** — auth через `OUROBOROS_NETWORK_PASSWORD` уже
  ограничивает доступ; если в production откроем без auth — добавим.

## Альтернативы рассмотренные

- **WebSocket-команда `/clear`** — отвергнуто, см. §«Почему REST» выше.
- **Удаление `dialogue_blocks.json` / scratchpad** при clear — отвергнуто,
  см. §«Почему НЕ truncate» выше.
- **Реюз `POST /api/reset`** с параметром `--keep-settings` — отвергнуто,
  endpoint декларирует «restart with onboarding», семантика другая;
  изменение его поведения сломает UI flow.
- **Truncate без confirm** — отвергнуто, история чата может быть ценной для
  пользователя; явный confirm с описанием что **не** удаляется — правильный
  UX.

## Тестирование

Smoke-тест на hermetic data dir:

```bash
echo '{"direction":"in","text":"X","ts":"...","chat_id":1}' > $DATA/logs/chat.jsonl
echo '{"direction":"out","text":"Y","ts":"...","chat_id":1}' >> $DATA/logs/chat.jsonl

# до:    /api/chat/history → messages: 2
# clear: POST /api/chat/clear → {"status":"ok","cleared":["chat.jsonl"]}
# после: /api/chat/history → messages: 0, chat.jsonl size = 0 bytes
```

UI handler не unit-тестирован (нет JS test runner'а в проекте) — будет
проверен вживую через server-deploy + браузер.

## Ссылки

- ADR-0001 (`docs/adr/0001-fork-ouroboros.md`) — почему consolidator выпилен
- ADR-0019 (`docs/adr/0019-system-prompt-analyst.md`) §«Что потерялось»
- `ouroboros/server_history_api.py` — `make_chat_clear_endpoint`
- `web/modules/chat.js` — UI handler
