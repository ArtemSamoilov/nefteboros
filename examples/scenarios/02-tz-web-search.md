# 02 Tz Web Search

## ТЗ-цитата

> ТЗ §4.6.2: «ответ на основе веб-поиска».

**Категория агента:** Web search — актуальная цена Brent

## Запрос (canonical под категорию §4.6)

```
Какая сейчас актуальная цена нефти Brent? Дай ссылки на источники.
```

## Метаданные прогона

- **prod version:** v2.3.5+ (commit `c3c22f6`, PR #48 `fix/auto-enable-web-search`)
- **server:** `ws://186.246.2.190:8000/ws` (Docker image `nefteboros:dev`, image built 2026-05-11 12:48 UTC)
- **time (UTC):** 2026-05-11 13:05:56
- **duration:** 35.1s
- **client_message_id:** `examples_tz-02_1778504756`
- **sender_session_id:** `examples_session_tz-02_1778504756`
- **Langfuse trace:** [`9263be9dbd02716e8b11bb0d416e00a3`](https://cloud.langfuse.com/trace/9263be9dbd02716e8b11bb0d416e00a3)
- **tools/skills:** `web_search`
- **observations:** 7
- **screenshot:** [`screenshots/scenario-tz-02.txt`](../screenshots/scenario-tz-02.txt) — CLI dump (см. ниже)

## Ответ агента (полный, как пришёл по WS)

По состоянию на утро понедельника, 11 мая 2026 года, фронтальный фьючерс Brent торгуется в районе **$105 за баррель** — рост на фоне срыва переговоров по иранскому урегулированию.

**Детали и источники:**

| Источник | Цена / событие | Время (UTC) |
|---|---|---|
| BBC — азиатская сессия | Brent **$105,50/баррель** (+4,1 %), затем небольшая коррекция | 09:01 |
| NYT — вечернее открытие | Brent **>$104/баррель** (+3 %+) в воскресенье вечером | 06:55 |

— [Oil prices jump after Trump dismisses Iran proposal to end war](https://www.bbc.com/news/articles/ckgp4ev4yg4o) — bbc.com, web  
— [Oil Prices Rise as Prospects for U.S.-Iran Peace Deal Fizzle](https://www.nytimes.com/2026/05/10/business/oil-prices-rise-as-prospects-for-us-iran-peace-deal-fizzle.html) — nytimes.com, web

**Важно:** то, что публикуют СМИ — это котировки фронтального фьючерса ICE Brent. Строгий спот (Dated Brent, поставка в dated-месяц) доступен только по подписке Argus/Platts и может отличаться на $0,50–1,50/баррель. EIA в последнем STEO (выпуск ~10 мая) фиксирует среднемесячную цену марта на уровне **$103/баррель** с апрельским пиком около **$128/баррель** — [Short-Term Energy Outlook](https://www.eia.gov/outlooks/steo/report/global_oil.php) — eia.gov, web.

## Web-источники (из trace)

- [Energy - Bloomberg - Bloomberg Markets](https://www.bloomberg.com/energy) — `www.bloomberg.com`
- [Short-Term Energy Outlook - U.S. Energy Information Administration (EIA)](https://www.eia.gov/outlooks/steo/report/global_oil.php) — `www.eia.gov`
- [Oil Prices Rise as Prospects for U.S.-Iran Peace Deal Fizzle - The New York Times](https://www.nytimes.com/2026/05/10/business/oil-prices-rise-as-prospects-for-us-iran-peace-deal-fizzle.html) — `www.nytimes.com`
- [Oil prices jump after Trump dismisses Iran proposal to end war](https://www.bbc.com/news/articles/ckgp4ev4yg4o) — `www.bbc.com`

## Прошёл ли сценарий ожидания ТЗ?

Категория §2.3 ТЗ — текущие котировки. Агент сразу идёт в `web_search` (Brave API, freshness=pd), без вызова RAG. Все источники с явной маркировкой `— domain, web`.
