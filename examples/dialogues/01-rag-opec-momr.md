# Диалог 1 — Запрос по отчёту (RAG)

**Категория:** RAG — ответ на основе отраслевого отчёта (§4.6.1)

**Summary:** Запрос о квотах OPEC. Агент честно фиксирует, что MOMR в RAG-корпусе нет, переключается на open-source данные через web + явная разметка `[…, p.X]` vs `[…](url) — domain, web`.

## Метаданные

- **trace_id:** `1032afac6f1780d8e2b049101c619175` ([Langfuse](https://cloud.langfuse.com/trace/1032afac6f1780d8e2b049101c619175))
- **session_id:** `chat:1`
- **timestamp (UTC):** 2026-05-11 12:00:07.567000+00:00
- **prod version:** v2.3.5+ (commit c3c22f6, PR #48)
- **tools/skills:** `rag_search`, `web_search`

## Запрос

```
Что говорит OPEC MOMR о квотах добычи на 2026 год?
```

## Ответ агента

**1. OPEC MOMR о квотах добычи на 2026 год**

**OPEC MOMR (Monthly Oil Market Report) в нашем RAG-корпусе из 25 документов отсутствует.** Прямую цитату из MOMR с allocation tables и footnotes дать не могу. В корпусе есть лишь косвенная ссылка: в `IEA Oil Market Report — April 2026` упоминается **Table 3b «World Oil Production (OPEC+ crude production based on current agreement)»**, но она доступна только подписчикам IEA `[IEA Oil Market Report — April 2026 (free version), p.62-66]`.

Ниже — headline-данные по формальным квотам из открытых источников (решение OPEC+ от **3 мая 2026**).

| Параметр | Детали |
|---|---|
| **Общий объём корректировки** | **+188 тыс. баррелей в сутки** с июня 2026 года `[Семь стран ОПЕК+ приняли решение увеличить квоты...](https://www.interfax.ru/business/1087396) — interfax.ru, web` |
| **Формат** | «Добровольные корректировки» (voluntary adjustments), участвуют 7 стран (без ОАЭ, которая официально вышла из картеля 1 мая 2026) `[Семь стран ОПЕК+ принципиально договорились...](https://www.interfax.ru/business/1087392) — interfax.ru, web` |
| **Агрегированный потолок** | **~34,744 млн б/с** — на ~185 тыс. б/с выше предыдущего уровня (с учётом компенсационных графиков) `[Семь стран ОПЕК+ приняли решение...](https://www.interfax.ru/business/1087396) — interfax.ru, web` |

**Распределение прироста (июнь 2026):**

| Страна | Прирост, тыс. б/с |
|---|---|
| Саудовская Аравия | +62 |
| Россия | +62 |
| Ирак | +26 |
| Кувейт | +16 |
| Казахстан | +10 |
| Алжир | +6 |
| Оман | +5 |
| **Итого** | **+188** |

`[Квота ради квоты: страны ОПЕК+ приняли чисто символическое решение](https://finance.rambler.ru/economics/56397915-kvota-radi-kvoty-strany-opek-prinyali-chisto-simvolicheskoe-reshenie/) — finance.rambler.ru, web`

**Контекст.** Reuters и tier-1 агентства характеризуют шаг как **«символический»** — страны Персидского залива физически не могут реализовать повышенные квоты из-за продолжающихся ограничений на экспорт через Hormuz, поэтому фактическая добыча остаётся существенно ниже номинальных целей `[Семь стран ОПЕК+ принципиально договорились...](https://www.interfax.ru/business/1087392) — interfax.ru, web`.

---

**2. Spot-цена Brent сегодня (11 мая 2026)**

**Brent (ICE front-month, июльский фьючерс): ~$104,0–105,5 / баррель** — гэп-ап на открытии торговой недели на фоне геополитических рисков.

| Источник | Время публикации (UTC) | Котировка |
|---|---|---|
| Interfax | **11:25** (~38 мин. назад) | **~$104** |
| РБК Украина | 06:25 | **$105,45** |
| РТ на русском | 06:26 | **>$105** |
| Mail.ru Finance | 01:46 | **$105,09** |
| APA.az | вчера 21:32 | **>$105** |

`[Нефть продолжает дорожать, Brent торгуется у $104 за баррель](https://www.interfax.ru/business/1088829) — interfax.ru, web`  
`[Цены на нефть Брент выросли до 105 долларов...](https://www.rbc.ua/ukr/news/tsini-naftu-rizko-zletili-pislya-vidpovidi-1778478059.html) — rbc.ua, web`  
`[Мировые цены на нефть превысили $105 за баррель...](https://finance.mail.ru/article/mirovyie-tsenyi-na-neft-prevyisili-105-za-barrel-na-fone-geopoliticheskih-riskov-69208243/) — finance.mail.ru, web`

**Важное уточнение по инструменту.** Все open-web источники показывают **фьючерсный front-month** (скорее всего июльский контракт ICE Futures Europe), а не **spot physical** (Dated Brent). Реальная спот-оценка физического рынка публикуется в подписных сервисах Argus Media или S&P Global Platts с небольшим лагом. Разброс между источниками ($104–$105,45) отражает внутридневную волатильность: рынок пересматривает risk premium в реальном времени на фоне заявлений о продолжении конфликта на Ближнем Востоке `[Мировые цены на нефть превысили $105...](https://finance.mail.ru/article/mirovyie-tsenyi-na-neft-prevyisili-105-za-barrel-na-fone-geopoliticheskih-riskov-69208243/) — finance.mail.ru, web`.

## RAG-источники (top-chunks)

- `iea_omr_2026-04_free__0023` · score=0.585
  - _«Libya and Iran held at most recent level through 2027.  DECD Americas excludes Mexico.  S Eurasia excludes Russia, Kazarkhstan, Zarrbaijan.  Other Asia excludes…»_
- `iea_omr_2026-04_free__0017` · score=0.578
  - _«<sup>1</sup> Measured as deliveries from refineries and primary stocks, comprises inland deliveries, international marine bunkers, refinery fuel, crude for dire…»_
- `iea_oil_2025__0028` · score=0.565
  - _«Table 3 WORLD OIL PRODUCTION (million barrels per day) 2022 2023 2024 2025 2026 2027 2028 2029 2030 OPEC Crude Oil Saudi Arabia 10.33 9.57 9.09 Iran 2.55 2.99 3…»_
- `opec_asb_2024__0010` · score=0.565
  - _«|                                      | 2019      | 2020     | 2021      | 2022      | 2023      | change<br>23/22 | |--------------------------------------|--…»_
- `iea_omr_2026-04_free__0016` · score=0.563
  - _«|                                                              |             |              |             |             |              | Table 1                …»_
- _… +3 ещё_

## Веб-источники (выдача brave-api)

- [Семь стран ОПЕК+ приняли решение увеличить квоты по добыче нефти в июне на 188 тыс. б/с](https://www.interfax.ru/business/1087396) — `www.interfax.ru`
- [Семь стран ОПЕК+ принципиально договорились увеличить квоты по добыче нефти в июне на 188 тыс. б/с](https://www.interfax.ru/business/1087392) — `www.interfax.ru`
- [Квота ради квоты: страны ОПЕК+ приняли чисто символическое решение - Рамблер/личные финансы](https://finance.rambler.ru/economics/56397915-kvota-radi-kvoty-strany-opek-prinyali-chisto-simvolicheskoe-reshenie/) — `finance.rambler.ru`
- [Страны ОПЕК+ повысили квоты на добычу нефти](https://ru.krymr.com/a/news-strany-opek-kvoty-dobycha-nefti/33748587.html) — `ru.krymr.com`
- [В июне 2026 года ОПЕК+ увеличит квоты на добычу нефти на 188 000 баррелей в сутки.](https://www.vietnam.vn/ru/opec-tang-han-ngach-san-luong-dau-them-188-000-thung-ngay-trong-thang-6-2026) — `www.vietnam.vn`
- [Снижение добычи нефти странами ОПЕК до минимума за 36 лет из-за конфликта с Ираном - 6 мая 2026 | ФОНТАНКА.ру](https://www.fontanka.ru/2026/05/06/76406523/) — `www.fontanka.ru`
- [Семь стран ОПЕК+ приняли решение увеличить квоты по добыче нефти в июне на 188 тыс. б/с - ROGTEC Magazine](https://www.rogtecmagazine.com/%D1%81%D0%B5%D0%BC%D1%8C-%D1%81%D1%82%D1%80%D0%B0%D0%BD-%D0%BE%D0%BF%D0%B5%D0%BA-%D0%BF%D1%80%D0%B8%D0%BD%D1%8F%D0%BB%D0%B8-%D1%80%D0%B5%D1%88%D0%B5%D0%BD%D0%B8%D0%B5-%D1%83%D0%B2%D0%B5%D0%BB%D0%B8/?lang=ru) — `www.rogtecmagazine.com`
- [Новак: страны ОПЕК+ обсуждали на прошлой встрече только увеличение квот](https://finance.mail.ru/article/novak-stranyi-opek-obsuzhdali-na-proshloj-vstreche-tolko-uvelichenie-kvot-69208194/) — `finance.mail.ru`
- [Мировые цены на нефть превысили $105 за баррель на фоне геополитических рисков](https://finance.mail.ru/article/mirovyie-tsenyi-na-neft-prevyisili-105-za-barrel-na-fone-geopoliticheskih-riskov-69208243/) — `finance.mail.ru`
- [Цена на нефть марки Brent превысила 105 долларов за баррель](https://ru.apa.az/birzha/cena-na-neft-marki-brent-prevysila-105-dollarov-za-barrel-650620) — `ru.apa.az`
- [Цены на нефть марок WTI и Brent резко выросли из-за геополитической напряженности на Ближнем Востоке](https://marketpower.pro/publications/mirovye-tseny-na-neft-rezko-vyrosli-na-fone-napriazhennosti-na-blizhnem-vostoke) — `marketpower.pro`
- [Цены на нефть Брент выросли до 105 долларов - на какие слова Трампа отреагировал рынок | РБК Украина](https://www.rbc.ua/ukr/news/tsini-naftu-rizko-zletili-pislya-vidpovidi-1778478059.html) — `www.rbc.ua`
- [Нефть Brent резко пошла вверх и превысила 105 долларов | «Карл Маркс»](https://karl-marks.ru/neft-brent-rezko-poshla-vverh-i-prevysila-105-dollarov/) — `karl-marks.ru`
- [Курс доллара на бирже онлайн. Графики курса евро, нефти Brent и доллара к рублю](https://xn----dtbfdbwspgnceulm.xn--p1ai/chart-online.php) — `xn----dtbfdbwspgnceulm.xn--p1ai`
- [Цена нефти марки Brent превысила $105 за баррель — РТ на русском](https://russian.rt.com/business/news/1630505-brent-rost-ice-neft) — `russian.rt.com`
- [Нефть продолжает дорожать, Brent торгуется у $104 за баррель](https://www.interfax.ru/business/1088829) — `www.interfax.ru`

## Что показывает

Главное — **поведение при отсутствии данных** (§2.1 ТЗ). Агент не выдумывает цитату из MOMR, а явным дисклеймером сообщает «MOMR в корпусе из 25 документов отсутствует». Дальнейшая часть ответа подтянута через `web_search` (interfax.ru, rambler.ru) — приоритизация по §2.4 ТЗ соблюдена: сначала RAG, при отсутствии — web с явной маркировкой источников.
