# Examples — диалоги и ТЗ-сценарии

Раздел собирает **реальные** примеры использования агента «Нефтегазовый аналитик» в трёх форматах:

1. **`dialogues/`** — выборка из Langfuse-трэйсов за последние 7 дней (тестовые прогоны через WS на prod). Покрывает 5 категорий §4.6 ТЗ + bonus многоинструментный + multi-turn + conflict-resolution.
2. **`scenarios/`** — 5 свежих прогонов под каждый подпункт ТЗ §4.6, выполненных 2026-05-11 на prod (commit `c3c22f6`, после v2.3.5).
3. **`screenshots/`** — WS dump'ы каждого scenario (запрос/ответ/timing). UI-скриншоты в формате `.png` не приложены — см. раздел [«О screenshots»](#о-screenshots) ниже.

Все данные прогонов и trace_id — детерминированно выгружены из Langfuse Cloud (`pk-lf-9885a2f4-…`), синтетика отсутствует.

---

## ⚠ Замечание по ТЗ §«примеры запросов»

ТЗ (`docs/tz/original.md`) даёт **5 категорий** (§4.6), но **не содержит буквальных формулировок запросов** — за исключением одного: _«спрогнозируй цену Brent на 3 месяца»_ (§2.5). Для scenarios я взял эту единственную буквальную цитату (scenario 04) + 4 канонических запроса под каждую категорию §4.6, что явно указано в каждом `scenarios/*.md`.

---

## Диалоги (Langfuse, 7 дней)

| # | Файл | Категория | trace_id | tools |
|---|---|---|---|---|
| 1 | [`01-rag-opec-momr.md`](dialogues/01-rag-opec-momr.md) | RAG (§4.6.1) | [`b24a9c15…`](https://cloud.langfuse.com/trace/b24a9c15ddd1c95561bf3df97acdd6d2) | rag_search + web_search |
| 2 | [`02-combo-rag-web.md`](dialogues/02-combo-rag-web.md) | Combo (§4.6.3) | [`8e38a759…`](https://cloud.langfuse.com/trace/8e38a759ca2352dea5ebd97dbd6f6a4a) | rag_search + web_search |
| 3 | [`03-forecast-brent-6m-auto-web.md`](dialogues/03-forecast-brent-6m-auto-web.md) | Forecast (§4.6.4) | [`24d4dfe6…`](https://cloud.langfuse.com/trace/24d4dfe6124e9343e9eada8bb5bed47e) | analyst_query + classify_intent + forecast_call + web_search + validate_citations |
| 4 | [`04-forecast-sarimax-honest-limitation.md`](dialogues/04-forecast-sarimax-honest-limitation.md) | Forecast (§4.6.4) с явным declaration ограничения | [`07392f8e…`](https://cloud.langfuse.com/trace/07392f8ecdd92dbe3ea10965a5d5c7fb) | analyst_query + classify_intent + forecast_call + validate_citations |
| 5 | [`05-multi-tool-sanctions-forecast.md`](dialogues/05-multi-tool-sanctions-forecast.md) | **Bonus** — многоинструментный (RAG + Web + Forecast) | [`ba81edd9…`](https://cloud.langfuse.com/trace/ba81edd9587ef3446f3f920c98b8e4cc) | analyst_query + classify_intent + forecast_call + web_search + validate_citations |
| 6 | [`06-web-only-current-brent.md`](dialogues/06-web-only-current-brent.md) | Web (§4.6.2) | [`9263be9d…`](https://cloud.langfuse.com/trace/9263be9dbd02716e8b11bb0d416e00a3) | web_search |
| 7 | [`07-refusal-known-observability-gap.md`](dialogues/07-refusal-known-observability-gap.md) | Refusal (§4.6.5) **с гэпом в Langfuse** | _(no trace)_ | _(none)_ |
| 8 | [`08-multi-turn-forecast-context.md`](dialogues/08-multi-turn-forecast-context.md) | **Bonus** — multi-turn (3 round, общий sender_session_id) | _см. файл_ | _см. файл_ |
| 9 | [`09-conflict-rag-vs-web.md`](dialogues/09-conflict-rag-vs-web.md) | **Bonus** — conflict resolution (CREA vs Argus в одном ответе) | [`2e848652…`](https://cloud.langfuse.com/trace/2e8486528a4a8270f0dbd79c2c4a41d1) | rag_search + web_search |

> ⚠ Диалог 7 — это **честная фиксация** известной регрессии: короткие refusal-диалоги (1-2 LLM round, ~10–15s) иногда теряются из-за async batch-flush Langfuse SDK 4.x. Сам ответ агента в WS-сессии корректный (запрос про погоду → отказ), отсутствует только trace, не функциональность. См. [`docs/changelog/2026-05-10-wsrunner-eval-observability.md`](../docs/changelog/2026-05-10-wsrunner-eval-observability.md).
>
> ⚠ Диалог 9 использует **тот же trace**, что в диалоге 02 — но с другим фокусом (conflict-handling между CREA и Argus). Координатор PR #61 одобрил такое переиспользование при условии разных фокусов.
>
> ⚠ **Известная regression presentation layer (диалоги 03/04/05):** в финальном ответе forecast-запросов присутствует строка про «галлюцинированные цитаты — метаданные pipeline не прошли внешнюю валидацию». Verified **systematic** — в 10/10 forecast traces за 7 дней. Backlog v2.4: фильтр validate_citations warnings перед user-facing answer.

---

## ТЗ-сценарии (свежий прогон 2026-05-11)

| # | Файл | ТЗ-пункт | Запрос | trace_id | Длительность |
|---|---|---|---|---|---|
| 1 | [`01-tz-rag-otchet.md`](scenarios/01-tz-rag-otchet.md) | §4.6.1 «ответ на основе отчёта» | _«Что говорит OPEC MOMR о квотах добычи на 2026 год? Дай цифры и ссылку на отчёт.»_ | [`fd71062e…`](https://cloud.langfuse.com/trace/fd71062e3176f3f3d85e0e9b19158f29) | 171.7s |
| 2 | [`02-tz-web-search.md`](scenarios/02-tz-web-search.md) | §4.6.2 «ответ на основе веб-поиска» | _«Какая сейчас актуальная цена нефти Brent? Дай ссылки на источники.»_ | [`9263be9d…`](https://cloud.langfuse.com/trace/9263be9dbd02716e8b11bb0d416e00a3) | 35.1s |
| 3 | [`03-tz-rag-plus-web.md`](scenarios/03-tz-rag-plus-web.md) | §4.6.3 «комбинированный ответ» | _«Какие санкции против российской нефти введены в 2025–2026 и как они повлияли на дисконт Urals? Объедини данные отчётов и свежих новостей.»_ | [`2e848652…`](https://cloud.langfuse.com/trace/2e8486528a4a8270f0dbd79c2c4a41d1) | 195.8s |
| 4 | [`04-tz-forecast-brent-3m.md`](scenarios/04-tz-forecast-brent-3m.md) | §4.6.4 «вызов расчётного модуля» (буквальная цитата §2.5) | _«Спрогнозируй цену Brent на 3 месяца.»_ | [`dd8cc6e3…`](https://cloud.langfuse.com/trace/dd8cc6e39dc5dcab0be96c81aff8892b) | 73.3s |
| 5 | [`05-tz-refusal.md`](scenarios/05-tz-refusal.md) | §4.6.5 «корректная обработка запроса вне компетенции» | _«Какая сегодня погода в Москве?»_ | _не подтянулся_ (см. dialogue 07) | 12.3s |

**Все 5 ТЗ-категорий покрыты.** Сценарий 4 содержит **буквальную цитату из ТЗ §2.5**, остальные — канонические запросы под §4.6, без выдумок. Все CLI dump'ы прогона — в [`screenshots/`](screenshots/).

---

## Метаданные прогона ТЗ-сценариев

| Параметр | Значение |
|---|---|
| **prod git commit** | `c3c22f6` (Merge PR #48 `fix/auto-enable-web-search`, после v2.3.5) |
| **Docker image build** | `nefteboros:dev` собран 2026-05-11 12:48 UTC (через 10 мин после коммита) |
| **Server URL** | `ws://186.246.2.190:8000/ws` (доступен извне; web UI на `http://186.246.2.190:8000/`) |
| **Прогон выполнен** | 2026-05-11 13:03–13:11 UTC |
| **Langfuse project** | `pk-lf-9885a2f4-987c-4a8a-87cb-5a8191c1bc83` @ `cloud.langfuse.com` |
| **Скрипт прогона** | минимальный python+websockets, см. оригинал в commit history (PR description) |

---

## О screenshots

Координатор ожидал `.png` UI-скриншотов. В этой итерации они **не приложены** — Chrome MCP / preview-инструменты, доступные сессии в claude-cli, не подключились к удалённому prod (`186.246.2.190:8000`) в окне работы. Вместо них в [`screenshots/`](screenshots/) лежат полные **WS dump'ы** каждого сценария: `scenario-tz-XX.txt` содержит точный JSON, отправленный по WS, точный ответ агента и timing-метаданные. Это **детерминированный** snapshot — воспроизводится дословно из `client_message_id` через Langfuse.

Если нужен UI screenshot — он быстро снимается из браузера: открыть `http://186.246.2.190:8000/`, отправить тот же запрос из любого scenario, сделать screenshot. Я могу подтянуть это в follow-up PR, если важно.

---

## Воспроизведение

```bash
# WS-прогон одного запроса
python -c "
import asyncio, json, websockets
async def go():
    async with websockets.connect('ws://186.246.2.190:8000/ws', max_size=10*1024*1024) as ws:
        await ws.send(json.dumps({
            'type':'chat',
            'content':'Спрогнозируй цену Brent на 3 месяца.',
            'sender_session_id':'demo','client_message_id':'demo-1'}))
        async for raw in ws:
            m = json.loads(raw)
            if m.get('type')=='chat' and m.get('role')=='assistant' and m.get('done'):
                print(m['content']); break
asyncio.run(go())
"
```

После — в [Langfuse](https://cloud.langfuse.com) traces в session `chat:1` за нужное окно времени.
