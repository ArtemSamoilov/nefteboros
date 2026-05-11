# Диалог 5 — Multi-tool: санкции + долгосрочный прогноз

**Категория:** Bonus — multi-tool combo (RAG + Web + Forecast)

**Summary:** Запрос объединяет три инструмента: RAG (документы по санкционной повестке), web (свежие новости), forecast (6-12m прогноз Urals). В одном trace — 12 observations с полной иерархией classify_intent → forecast_call → validate_citations.

> ⚠ **Известная регрессия presentation layer (backlog v2.4):** в финальном ответе агента присутствует строка «Системное предупреждение: …метаданные модели (`[ADR-0024]`, `[forecast_model:*]`) помечены как потенциально «галлюцинированные цитаты» — формальная внешняя валидация меток pipeline не подтверждена.». Это **leak internal state валидатора в user-facing message**. Verified **systematic**: фраза «галлюцинированные цитаты — метаданные pipeline не прошли внешнюю валидацию» присутствует в **10 из 10** multi-tool forecast traces за последние 7 дней (см. также диалоги 03 и 04). Issue фиксируется как **известный bug**; правка не входит в scope текущего PR.

## Метаданные

- **trace_id:** `ba81edd9587ef3446f3f920c98b8e4cc` ([Langfuse](https://cloud.langfuse.com/trace/ba81edd9587ef3446f3f920c98b8e4cc))
- **session_id:** `chat:1`
- **timestamp (UTC):** 2026-05-10 13:47:35.315000+00:00
- **prod version:** v2.3.5+ (commit c3c22f6, PR #48)
- **tools/skills:** `web_search`, `analyst_query`, `classify_intent`, `forecast_call`, `validate_citations`

## Запрос

```
Как санкции 2026 влияют на спот-цену Urals и долгосрочный прогноз через 6-12 месяцев?
```

## Ответ агента

**Спот-цена Urals и прогноз в условиях санкций 2026**

**Текущая спот-цена (фиксация 8 мая 2026):**
- **Urals (CFR Med): $84,29 / баррель**
- **Urals/ESPO Blend (оценка Минфина РФ): $86,71 / баррель** — формула `0,78×Urals + 0,22×ESPO`
- Спред к Brent (ICE): **$17,00** (Brent @ $101,29)

`[Forecast: ou_regime, scenario=base, CI 80/95%]`

---

**Санкционный ландшафт 2026 (актуальный контекст):**

- **20-й пакет ЕС** (утверждён 23 апреля 2026): более трёх десятков ограничений, включая танкеры, банки и аммиак, однако **полный запрет на обслуживание вывоза российской нефти отложен** — вероятно, перенесён в 21-й пакет `[20-й пакет санкций ЕС против РФ: ограничения на танкеры, банки и аммиак](https://www.vedomosti.ru/politics/articles/2026/04/23/1192623-20-i-paket-sanktsii) — vedomosti.ru, web`.
- ЕС меняет стратегию: удар по каналам обхода через третьи страны (включая Кыргызстан), одновременно усиливая давление на нефтегазовые доходы и финансовую систему РФ `[ЕС меняет стратегию санкций против РФ: что в 20-м пакете](https://www.dw.com/ru/evrosouz-menaet-strategiu-sankcij-protiv-rossii-cta-v-20m-pakete/a-76918423) — dw.com, web`.
- **Американские санкции** против российской нефти возобновлены в апреле 2026; Россия заявила, что не будет поставлять нефть в страны, поддерживающие price cap `[Politico: Американские санкции против российской нефти возобновлены](https://vz.ru/news/2026/4/15/1410890.html) — vz.ru, web`.
- ЕС формально запретил ~90% импорта российской нефти `[Санкции в энергетической отрасли](https://commission.europa.eu/topics/eu-solidarity-ukraine/eu-sanctions-against-russia-following-invasion-ukraine/sanctions-energy_ru) — commission.europa.eu, web`.
- Price cap G7 на российскую нефть продолжает действовать: исторически Urals часто торговался **ниже $41/баррель** (референс для сравнения), текущий спот в $84+ отражает глобальный shock-режим на рынке, а не отмену скидки `[ЕС отказался от запрета на перевозку российской нефти](https://nsn.fm/policy/ekspert-obyasnil-pochemu-es-otkazalsya-ot-sanktsii-protiv-rossii) — nsn.fm, web`.

---

**Долгосрочный прогноз (единственный доступный горизонт — 12 месяцев):**

| Актив | Spot (8 мая) | Прогноз 12m (май 2027) | CI 80% | CI 95% |
|---|---|---|---|---|
| Urals (CFR Med) | $84,29 | **$85,50** | $70,83 – $100,16 | $63,08 – $107,91 |
| Urals/ESPO Blend (Минфин) | $86,71 | **$87,65** | $72,62 – $102,68 | $64,67 – $110,63 |
| Спред Brent–Urals | $17,00 | **$17,85** | — | — |

Модель: `ou_regime` (mean-reverting OU с regime conditioning, ADR-0024). Скорость реверсии θ = 2/год (half-life ≈ 4,2 мес), волатильность Urals σ = 27%/год. Long-run target μ(t) для Urals: $85,05 — на 12-месячном горизонте mean reversion почти полностью реализована.

---

**Ключевые ограничения и оговорки:**

1. **Прогноз на 6 месяцев отсутствует** — инструмент выдал только 12m base-case. Запрошенные bear/bull и 6m-горизонт не сгенерированы; для стресс-тестирования требуется ручной overlay из отчётов OPEC/IEA и геополитических сценариев (в backlog).
2. **Системное предупреждение:** внутренние метаданные модели (`[ADR-0024]`, `[forecast_model:*]`) помечены как потенциально «галлюцинированные цитаты» — формальная внешняя валидация меток pipeline не подтверждена.
3. **Методология:** запрошен ранее SARIMAX, однако текущая конфигурация маршрутизирует в `ou_regime`. Сравнение точности (MAPE, RMSE, покрытие CI) недоступно — `backtest_summary: null`.
4. **Санкции vs цена:** текущий спот Urals в $84+ существенно выше исторических уровней под price cap, но это отражает глобальный supply shock (Hormuz, Иран) и рост Brent, а не ослабление дисконта. Дисконт Urals к Brent стабилен ~$17. Прогнозируемое расширение спреда до $17,85 на 12m консервативно закладывает сохранение текущей санкционной архитектуры без эскалации (отсутствие 21-го пакета в модели).

**Bottom line:** в base-сценарии текущего shock-equilibrium санкции 2026 удерживают структуру рынка, но не создают дополнительного давления на спред Urals–Brent в ближайший год — модель ожидает практически флетовую динамику Urals с узким доверительным интервалом. Любое резкое усиление санкций (21-й пакет ЕС, расширение OFAC) потребует внешнего сценарного overlay.

## Веб-источники (выдача brave-api)

- [20-й пакет санкций ЕС против РФ: ограничения на танкеры, банки и аммиак - Ведомости](https://www.vedomosti.ru/politics/articles/2026/04/23/1192623-20-i-paket-sanktsii) — `www.vedomosti.ru`
- [Politico: Американские санкции против российской нефти возобновлены :: Новости дня / ВЗГЛЯД](https://vz.ru/news/2026/4/15/1410890.html) — `vz.ru`
- [Санкции в энергетической отрасли - Европейская комиссия](https://commission.europa.eu/topics/eu-solidarity-ukraine/eu-sanctions-against-russia-following-invasion-ukraine/sanctions-energy_ru) — `commission.europa.eu`
- [Сюрпризы "20 пакетика": Европейские санкции пока обошли запрет на транспортировку российской нефти - Рамблер/личные финансы](https://finance.rambler.ru/economics/56364509-syurprizy-20-paketika-evropeyskie-sanktsii-poka-oboshli-zapret-na-transportirovku-rossiyskoy-nefti/) — `finance.rambler.ru`
- [Цена Нефти Urals Сегодня $0.00/Баррель — Спред Urals-Brent](https://www.oilpriceapi.com/ru/urals-crude-price) — `www.oilpriceapi.com`
- [ЕС меняет стратегию санкций против РФ: что в 20-м пакете](https://www.dw.com/ru/evrosouz-menaet-strategiu-sankcij-protiv-rossii-cto-v-20m-pakete/a-76918423) — `www.dw.com`
- [ЕС согласовал новые санкции против России, но введение запрета на морские перевозки отложено | Евронью́с](https://ru.euronews.com/my-europe/2026/04/23/es-soglasoval-novye-sankcii-protiv-rossii) — `ru.euronews.com`
- [ЕС отказался от запрета на перевозку российской нефти](https://nsn.fm/policy/ekspert-obyasnil-pochemu-es-otkazalsya-ot-sanktsii-protiv-rossii) — `nsn.fm`

## Регрессия presentation layer (см. выше Summary)

В пункте 2 раздела «Ключевые ограничения и оговорки» (выше в ответе) видно текст, который должен был остаться **внутри** validate_citations и не дойти до пользователя:

> «**Системное предупреждение:** внутренние метаданные модели (`[ADR-0024]`, `[forecast_model:*]`) помечены как потенциально «галлюцинированные цитаты» — формальная внешняя валидация меток pipeline не подтверждена.»

Корень — в synthesize-узле analyst_query: warnings из `validate_citations.validation_warnings` встраиваются в финальный markdown вместо того чтобы оставаться в trace metadata. Backlog v2.4 — отдельная задача presentation-layer фильтра перед отправкой user-facing answer.

## Что показывает

**Самый сложный пример** — стресс-кейс на ТЗ-приоритизацию. Запрос про санкции (контекст в RAG), их свежесть (web), и forecast на 6-12m (расчётный модуль). Агент собирает 12 observations в один user_request trace. Полезен для оценки, как roadmap-уровень multi-tool scenarios уже работает в проде.
