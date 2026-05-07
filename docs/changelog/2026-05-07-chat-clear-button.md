# Changelog: chat-clear-button — кнопка «New chat» и `/api/chat/clear`

- **Дата:** 2026-05-07
- **PR:** `feature/chat-clear-button`
- **ADR:** [docs/adr/0020-chat-clear-button.md](../adr/0020-chat-clear-button.md)
- **Связанные:** ADR-0001 (выпиленный consolidator), ADR-0019 (где подняли проблему длинного thread'а)

## Задача

В UI Ouroboros'а на сервере (`http://186.246.2.190:8765/`, deploy от PR #18)
весь диалог с агентом — один непрерывный thread. Без кнопки «новый чат»
история накапливается линейно, после ~50-80 обменов context упрётся в cap
LLM. `POST /api/reset` существует, но удаляет всё runtime (settings, memory,
state) — слишком destructive.

Артём попросил минимально удобное решение: кнопка «новый чат» которая чистит
видимую историю, но сохраняет всё остальное.

## Решение (см. ADR-0020)

`POST /api/chat/clear` + UI кнопка в chat-header.

## Файлы

**Изменено:**

- `ouroboros/server_history_api.py` — `make_chat_clear_endpoint(data_dir)`
  фабрика handler'а. Truncate'ит `logs/chat.jsonl` + `logs/progress.jsonl`
  через `write_text("")`. Возвращает JSON со списком очищенных файлов.
- `server.py` — import + создание `api_chat_clear` + регистрация Route
  `POST /api/chat/clear` (рядом с `/api/chat/history`).
- `web/modules/chat.js`:
  - Новая кнопка `data-chat-command="new-chat"` между `Review` и `Restart` в
    `chat-header-actions`.
  - Handler в `headerActions` listener'е: `confirm()` → `fetch POST` →
    `messagesDiv.innerHTML = ""` + restore `typingEl` → `syncHistory({fromReconnect: true})`.
  - На error — `alert()`.

**Добавлено:**

- `docs/adr/0020-chat-clear-button.md`
- `docs/changelog/2026-05-07-chat-clear-button.md`

**Не затронуто:**

- `settings.json` (network password, провайдер, runtime mode)
- `memory/*` — `identity.md`, `scratchpad.md`, `knowledge/`, `dialogue_blocks.json`
- `state/*` — skill enable, advisory review
- `logs/events.jsonl`, `logs/tools.jsonl`, `logs/supervisor.jsonl` — execution
  traces, нужны для постмортема и cost breakdown
- `web/index.html`, `web/style.css` — кнопка использует существующий
  `chat-header-btn` класс

## Тесты

### AST

`ouroboros/server_history_api.py` + `server.py` — оба валидны.

### Smoke на hermetic data dir

```bash
$ echo '{"direction":"in","text":"X","ts":"...","chat_id":1}' > $DATA/logs/chat.jsonl
$ echo '{"direction":"out","text":"Y","ts":"...","chat_id":1}' >> $DATA/logs/chat.jsonl

$ curl http://127.0.0.1:8780/api/chat/history | jq '.messages | length'
2

$ curl -X POST http://127.0.0.1:8780/api/chat/clear
{"status":"ok","cleared":["chat.jsonl"]}

$ curl http://127.0.0.1:8780/api/chat/history | jq '.messages | length'
0

$ wc -c $DATA/logs/chat.jsonl
0
```

### UI

Не unit-тестирован (нет JS-runner'а в проекте). Будет проверен вживую на
сервере: `git pull && systemctl restart nefteboros` → открыть
`http://186.246.2.190:8765/` → нажать `New chat` → confirm → история
очищается визуально и в `chat.jsonl`.

## Deployment notes

После merge на сервер:

```bash
ssh -i ~/.ssh/id_ed25519_nefteboros root@186.246.2.190 "
  cd /root/nefteboros && \
  git pull --ff-only origin main && \
  systemctl restart nefteboros && \
  systemctl is-active nefteboros
"
```

Никаких pip-зависимостей, миграций или env-переменных не требуется. После
restart обновится UI bundle (статика), кнопка появится в Chat header.

## Связанные

- [ADR-0020](../adr/0020-chat-clear-button.md) — обоснование решения
- ADR-0019 §«Что потерялось при выпиле» — там был поднят вопрос длинного thread'а
- `ouroboros/server_history_api.py` — endpoint
- `web/modules/chat.js` — UI handler
