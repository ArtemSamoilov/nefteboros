# 2026-05-08 — Fix кнопки «New chat»: empty server history vs fallback на sessionStorage

**PR:** `fix/new-chat-empty-history-fallback`
**Связано:** PR #19 (chat-clear-button), PR #23 (new-chat-card-cleanup hard reload).

## Симптом

Артём нажимает «New chat», подтверждает confirm — старый разговор остаётся в UI, ничего визуально не меняется.

## Расследование

Server-side всё работает: `POST /api/chat/clear` обнуляет `chat.jsonl` и `progress.jsonl`, `GET /api/chat/history` возвращает `{messages: []}` с `Cache-Control: no-store`.

Frontend `web/modules/chat.js`:

1. Кнопка [chat.js:1499](../../web/modules/chat.js) — `POST /api/chat/clear` → `window.location.reload()` ✅.
2. После reload bootstrap IIFE [chat.js:1252](../../web/modules/chat.js):

   ```js
   if (await syncHistory({ includeUser: true })) return;  // success → done
   const saved = JSON.parse(sessionStorage.getItem(CHAT_STORAGE_KEY) || '[]');
   for (const msg of saved) addMessage(...);              // fallback
   ```

3. `syncHistory()` [chat.js:1230](../../web/modules/chat.js):

   ```js
   return messages.length > 0;  // ← баг
   ```

Логика возврата смешивала «успех fetch» с «есть ли что-то нарисовать». На пустой истории (после `New chat`) `syncHistory()` возвращал **false**, IIFE интерпретировал это как «server недоступен» и подтягивал прошлый разговор из `sessionStorage` (наполненный во время предыдущей сессии через `persistVisibleHistory()` [chat.js:333](../../web/modules/chat.js)).

UI выглядел идентично прежнему — будто кнопка ничего не сделала, хотя server-side всё было очищено.

## Решение

Два изменения в `web/modules/chat.js`:

### 1. `syncHistory` — `return true` на любой успешный fetch

```js
// Было:
return messages.length > 0;

// Стало:
return true;  // success-of-fetch, не «есть ли что нарисовать»
```

Единственный caller `syncHistory`, который читает return value — bootstrap IIFE. Остальные вызовы используют `.catch(() => {})` или игнорируют return. Изменение безопасно.

### 2. Defence-in-depth — очистка `sessionStorage` в `new-chat` handler

```js
try { sessionStorage.removeItem(CHAT_STORAGE_KEY); } catch {}
window.location.reload();
```

Если в будущем какой-то edge-case снова откроет fallback-путь (network blip, broken CSP, etc), `sessionStorage` уже пуст — кнопка не «откатит» очистку.

## Тесты

`pytest tests/test_chat_logs_ui.py`: **60 passed** (statiс-check тесты на инварианты `chat.js`, ничего не ломает).

Frontend unit-тестов нет; проверка — manual smoke на сервере после deploy.

## Deployment

`git pull && systemctl restart nefteboros`. ES-modules под `/static/` отдаются с `Cache-Control: no-cache, must-revalidate` ([server_web.py:38](../../ouroboros/server_web.py)) — браузер revalidates через etag и подтянет свежий `chat.js` после reload. **Hard refresh** (Cmd+Shift+R) рекомендован для гарантированного обхода service worker / browser cache.
