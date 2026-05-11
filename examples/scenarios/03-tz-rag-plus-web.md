# 03 Tz Rag Plus Web

## ТЗ-цитата

> ТЗ §4.6.3: «комбинированный ответ».

**Категория агента:** RAG + Web — санкции + дисконт Urals

## Запрос (canonical под категорию §4.6)

```
Какие санкции против российской нефти введены в 2025–2026 и как они повлияли на дисконт Urals? Объедини данные отчётов и свежих новостей.
```

## Метаданные прогона

- **prod version:** v2.3.5+ (commit `c3c22f6`, PR #48 `fix/auto-enable-web-search`)
- **server:** `ws://186.246.2.190:8000/ws` (Docker image `nefteboros:dev`, image built 2026-05-11 12:48 UTC)
- **time (UTC):** 2026-05-11 13:06:34
- **duration:** 195.8s
- **client_message_id:** `examples_tz-03_1778504794`
- **sender_session_id:** `examples_session_tz-03_1778504794`
- **Langfuse trace:** [`2e8486528a4a8270f0dbd79c2c4a41d1`](https://cloud.langfuse.com/trace/2e8486528a4a8270f0dbd79c2c4a41d1)
- **tools/skills:** `rag_search`, `web_search`
- **observations:** 19
- **screenshot:** [`screenshots/scenario-tz-03.txt`](../screenshots/scenario-tz-03.txt) — CLI dump (см. ниже)

## Ответ агента (полный, как пришёл по WS)

## Санкции против российской нефти 2025–2026 и влияние на дисконт Urals

### 1. Ключевые санкционные меры

**Продолжение базовых ограничений:**
- **ЕС**: эмбарго на импорт российской сырой нефти (действует с 2022 г.), запрет на страхование и финансирование перевозок выше price cap.
- **G7 + Австралия**: механизм price cap, сниженный в **сентябре 2025** до **$47,6/баррель** [September 2025 — Monthly analysis of Russian fossil fuel exports and sanctions](https://energyandcleanair.org/september-2025-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) — energyandcleanair.org, web.

**Новые ограничения 2025–2026:**
- **Ноябрь 2025**: OFAC США ввёл санкции против **«Роснефти»** и **«Лукойла»** — двух крупнейших экспортёров. По оценкам аналитиков, это вызвало резкое увеличение дисконта, поскольку покупатели получили рычаги для выторговки более глубоких скидок [США отчитались о санкциях против «Лукойла» и «Роснефти»](https://www.forbes.ru/investicii/550231-ssa-otcitalis-o-sankciah-protiv-lukojla-i-rosnefti-kak-dejstvovat-investoram) — forbes.ru, web; [U.S. Sanctions on Rosneft and Lukoil](https://freepolicybriefs.org/2025/12/08/eu-russia-oil-sanctions/) — freepolicybriefs.org, web.
- **Осень 2025**: 19-й пакет санкций ЕС расширил ограничения на третьесторонние банки и сервисы, обслуживающие российскую энергетику.
- **2026**: фокус сместился на **исполнение** существующих мер — ограничение «теневого флота» (shadow fleet), усиление мониторинга price cap и страховых сертификатов.

### 2. Динамика дисконта Urals: данные разных источников

Дисконт Urals к Brent в 2025–2026 гг. демонстрирует экстремальную волатильность — цифры разнятся в зависимости от методологии (среднемесячный экспортный трекинг CREA vs спотовые котировки Argus/Bloomberg).

#### CREA (Centre for Research on Energy and Clean Air) — ежемесячный мониторинг экспорта

| Период | Дисконт Urals к Brent | Источник |
|---|---|---|
| Апрель 2025 | **$2,40** (самый узкий спред за год+) | [April 2025 — CREA](https://energyandcleanair.org/april-2025-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) |
| Ноябрь 2025 | **$6,66** (+4% м/м) | [November 2025 — CREA](https://energyandcleanair.org/november-2025-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) |
| Январь 2026 | **$9,85** (+5% м/м) | [January 2026 — CREA](https://energyandcleanair.org/january-2026-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) |
| Март 2026 | **$6,40** (сократился вдвое м/м) | [March 2026 — CREA](https://energyandcleanair.org/march-2026-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) |

#### Спотовые котировки и рыночные срезы (Argus / Bloomberg)

| Период | Показатель | Источник |
|---|---|---|
| Октябрь → Декабрь 2025 | Спред расширился с **~$12** до **>$26** | [Stiffening European sanctions against the Russian oil trade](https://www.brookings.edu/articles/stiffening-european-sanctions-against-the-russian-oil-trade/) — brookings.edu, web |
| Декабрь 2025 | Дисконт **~$27** (Argus); цена Urals упала до **~$34** | [Российская нефть упала до минимума](https://nv.ua/world/countries/rossiyskaya-neft-upala-do-minimuma-sankcii-povliyali-na-ceny-v-2025-godu-50570545.html) — nv.ua, web; [Bloomberg: цена российской нефти Urals обвалилась до $34](https://www.profinance.ru/news2/2025/12/22/ci43-bloomberg-tsena-rossijskoj-nefti-urals-obvalilas-do-34-za-barrel.html) — profinance.ru, web |
| Апрель 2026 | Дисконт **$25,60** (среднее за 10 дней, Argus NSD) против $28,30 за весь март | [Скидки на нефть РФ](https://www.finmarket.ru/news/6598767) — finmarket.ru, web |

*Примечание: цифры Argus и CREA несопоставимы напрямую — разные базы оценки (спот FOB vs средневзвешенный экспорт), разные контрактные спецификации и тайминги.*

### 3. Аналитическая оценка из отчётов

**Bruegel Working Paper 32/2025** (think-tank, европейская макроаналитика) фиксирует, что увеличение разрыва Brent–Urals после введения price cap в 2023 г. было **временным**: как только обход санкций через третьи страны и «теневой флот» стал массовым, спред начал сжиматься [Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap, p.13-17].

В то же время Bruegel подчёркивает, что российские доходы остаются уязвимыми: изменение цены Urals на **$10/баррель** транслируется в колебания федеральных доходов на **1–1,5% ВВП** (оценка по данным 2019–2024 гг.) [Bruegel Working Paper 32/2025, p.13-17].

**OPEC World Oil Outlook 2025** констатирует структурный сдвиг торговых потоков: поставки российской нефти в Европу упали до ~**1,6 мб/д** (против довоенных уровней), тогда как перенаправление в Азиатско-Тихоокеанский регион обеспечило выход, хотя и по дисконтированным ценам [OPEC World Oil Outlook 2025 (full), p.256-262].

### 4. Почему дисконт так волатилен

1. **Санкционные шоки = резкие скачки**. OFAC против Роснефти/Лукойла в ноябре 2025 и ценовые коридоры price cap вызывали краткосрочные панические расширения спреда до $25–28.
2. **Адаптация и обход**. Теневой флот, переуступки через третьи юрисдикции и бартерные схемы позволяют частично нейтрализовать эффект — отсюда сжатие дисконта до $6–10 в «спокойные» месяцы (март 2026).
3. **Переключение спроса**. Азиатские покупатели (Индия, Китай) используют санкционное давление как аргумент для выторговки скидок, но их аппетит ограничен логистикой и страховкой.

### 5. Итог

Санкции 2025–2026 гг. не создали линейно растущего дисконта, а породили **режим высокой волатильности**: $2–6 в периоды адаптации, $25–28 при каждом новом санкционном ударе (OFAC, ужесточение cap). Bruegel оценивает этот эффект как **частично нейтрализуемый обходом**, но бюджетная чувствительность России остаётся значительной. Различие в оценках дисконта (CREA vs Argus) отражает фрагментацию рынка: средневзвешенный экспортный ценник и спот FOB Novorossiysk/Primorsk расходятся на $15–20 из-за разного состава покупателей, фрахта и риск-премий.

## RAG top-chunks (из trace)

- `ei_statistical_review_2025__0050` · score=0.613
- `ei_statistical_review_2025__0054` · score=0.598
- `opec_woo_2025__0056` · score=0.594
- `ei_statistical_review_2025__0051` · score=0.593
- `ei_statistical_review_2025__0056` · score=0.592
- _… +13 ещё_

## Web-источники (из trace)

- [Скидки на нефть РФ за время ослабления санкций США упали почти на $3/барр., в Азии скидки сменились премиями - Argus - ИА "Финмаркет"](https://www.finmarket.ru/news/6598767) — `www.finmarket.ru`
- [Цена нефти на месяц: прогноз для Brent и Urals — SberCIB](https://sbercib.ru/publication/rinok-nefti-vliyanie-konflikta-na-blizhnem-vostoke-i-blokadi-ormuzskogo-proliva) — `sbercib.ru`
- [Цена нефти Urals в 2026 году: прогнозы и экспорт](https://www.kommersant.ru/doc/8337686) — `www.kommersant.ru`
- [Российская нефть упала до минимума — санкции повлияли на цены в 2025 году / NV](https://nv.ua/world/countries/rossiyskaya-neft-upala-do-minimuma-sankcii-povliyali-na-ceny-v-2025-godu-50570545.html) — `nv.ua`
- [США отчитались о санкциях против «Лукойла» и «Роснефти»: как действовать инвесторам | Forbes.ru](https://www.forbes.ru/investicii/550231-ssa-otcitalis-o-sankciah-protiv-lukojla-i-rosnefti-kak-dejstvovat-investoram) — `www.forbes.ru`
- [Санкции США вновь ударили по ценам на российскую нефть: Рынки: Экономика: Lenta.ru](https://tve24.ru/news/2025/12/22/sanktsii-ssha-vnov-udarili-po-tsenam-na-rossiyskuyu-neft/) — `tve24.ru`
- [Прогноз цен на нефть на 2026 год — аналитический обзор SberCIB](https://sbercib.ru/publication/neftyanoi-rinok-v-2026-godu-analiticheskii-obzor-i-prognoz) — `sbercib.ru`
- [Цена Нефти Urals Сегодня $0.00/Баррель — Спред ...](https://www.oilpriceapi.com/ru/urals-crude-price) — `www.oilpriceapi.com`
- [Будет ли у санкций США против российской нефти накопительный эффект и почему нужно возвратиться к налоговой реформе в отрасли? - Российская газета](https://rg.ru/2026/01/03/glava-fneb-simonov-k-voprosu-o-nalogah-v-neftianoj-otrasli-neobhodimo-vernutsia.html) — `rg.ru`
- [Bloomberg: цена российской нефти Urals обвалилась до $34 за баррель | ProFinance.Ru](https://www.profinance.ru/news2/2025/12/22/ci43-bloomberg-tsena-rossijskoj-nefti-urals-obvalilas-do-34-za-barrel.html) — `www.profinance.ru`
- [March 2026 — Monthly analysis of Russian fossil fuel exports and sanctions – Centre for Research on Energy and Clean Air](https://energyandcleanair.org/march-2026-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) — `energyandcleanair.org`
- [April 2025 — Monthly analysis of Russian fossil fuel exports and sanctions – Centre for Research on Energy and Clean Air](https://energyandcleanair.org/april-2025-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) — `energyandcleanair.org`
- [November 2025 — Monthly analysis of Russian fossil fuel exports and sanctions – Centre for Research on Energy and Clean Air](https://energyandcleanair.org/november-2025-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) — `energyandcleanair.org`
- [Stiffening European sanctions against the Russian oil trade | Brookings](https://www.brookings.edu/articles/stiffening-european-sanctions-against-the-russian-oil-trade/) — `www.brookings.edu`
- [Oil Prices Have Fallen: Next year, Russia is very likely to have to live with oil prices in the range of $40–45 per barrel](https://re-russia.net/en/analytics/0368/) — `re-russia.net`
- [January 2026 — Monthly analysis of Russian fossil fuel exports and sanctions – Centre for Research on Energy and Clean Air](https://energyandcleanair.org/january-2026-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) — `energyandcleanair.org`
- [U.S. Sanctions on Rosneft and Lukoil: Pressure on Moscow, Strains on Europe • FREE NETWORK](https://freepolicybriefs.org/2025/12/08/eu-russia-oil-sanctions/) — `freepolicybriefs.org`
- [September 2025 — Monthly analysis of Russian fossil fuel exports and sanctions – Centre for Research on Energy and Clean Air](https://energyandcleanair.org/september-2025-monthly-analysis-of-russian-fossil-fuel-exports-and-sanctions/) — `energyandcleanair.org`

## Прошёл ли сценарий ожидания ТЗ?

Эталонная демонстрация §2.4 ТЗ: запрос требует и историческую базу (санкционные пакеты в RAG) и актуальность (как именно влияют на дисконт сейчас, через web). В trace — 19 observations, 2 параллельных tool-loops.
