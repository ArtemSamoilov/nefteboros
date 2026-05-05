# ADR-0008 — LLM max_tokens default: 16384 → 4096

- **Дата:** 2026-05-05
- **Статус:** Принято
- **Контекст:** PR `feature/server-fixes`, развёртывание на Timeweb

## Контекст

Upstream Ouroboros использовал `max_tokens=16384` в дефолте `LLMClient.chat()` и связанных методах. Это работает с моделями OpenAI/Anthropic (у них лимиты 8K-200K). Но при первом запуске на сервере с подключённым HydraGPT (`https://hydragpt.ru/v1`) и моделью `kimi-k2p6` мы получили:

```
BadRequestError("Error code: 400 - {'error': {'message':
  'Requests with max_tokens > 4096 must have stream=true',
  'param': 'max_tokens', 'code': 'BAD_REQUEST', 'type': 'error'}, ...}")
```

12 фейлов подряд при первом тесте чата. Ouroboros отрабатывал fallback-цепочку (kimi → claude → openai), все падали — kimi из-за лимита, остальные из-за отсутствия ключа.

## Решение

Снизить дефолт `max_tokens` в `ouroboros/llm.py` с 16384 до **4096** в двух точках:
- `LLMClient.chat()` (строка 530)
- `LLMClient.chat_with_target()` (строка 562)

Третья точка (`LLMClient.vision_query`, строка 1556) уже была 4096 — не трогаем.

## Аргументация

**Почему 4096:**
- Покрывает рабочие сценарии аналитика: ответ + tool_calls в reasoning-моделях обычно укладываются в 4K
- Совместим с лимитами всех HydraGPT-моделей без необходимости включать streaming
- Прохождение через стандартный Bot API без overhead'а SSE-парсинга
- Минимально инвазивная правка относительно upstream

**Почему не включить streaming:**
- Streaming в Ouroboros реализован, но для tool-loop с многошаговым reasoning'ом усложняет cost/latency tracking
- Streaming при выпавшем соединении мусорит частичный ответ — для наших отчётов с цитатами риск выше
- Если потом понадобится — добавим env-flag `OUROBOROS_STREAM=true` в отдельном PR без правки дефолтов

**Почему не env var (`OUROBOROS_MAX_TOKENS`):**
- Не нашёл подтверждения, что Ouroboros читает такую переменную в `chat()` сигнатуре
- Добавление env var требует ещё нескольких правок в loop_llm_call.py — out of scope этого фикса
- 4096 как дефолт безопаснее: единая точка правды, нет «забыл выставить env»

## Последствия

**Плюсы:**
- HydraGPT-модели работают без BadRequest
- Чат на сервере отвечает (подтверждено 2026-05-05)
- Меньший дефолт ⇒ меньший cost-cap на запрос ⇒ меньший риск runaway

**Минусы:**
- Если кто-то запустит наш форк с OpenAI/Claude и захочет 16K-вых ответов — придётся явно передавать `max_tokens=` в вызов
- Reasoning-модели (kimi/glm/deepseek) в HydraGPT могут не поспеть с длинным ответом за 4096 (видим `finish_reason: length` в curl-тестах). На практике — для аналитика этого хватает; если ответы стабильно обрезаются, поднимем до 6K-8K с `stream=true`.

## Альтернативы рассмотренные

- **Streaming-mode default** — отвергнуто (см. выше).
- **Per-provider max_tokens map** — over-engineering на старте, можно вернуться когда увидим реальные обрывы по разным моделям.
- **Просто env var** — отвергнуто (multi-point правки, out of scope).

## Ссылки

- Логи фейлов: server log на 2026-05-04 21:00 (Timeweb 186.246.2.190)
- HydraGPT API doc: <https://hydragpt.ru>
- Этот PR: docs/changelog/2026-05-05-server-fixes.md
