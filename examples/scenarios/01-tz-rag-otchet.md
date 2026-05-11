# 01 Tz Rag Otchet

## ТЗ-цитата

> ТЗ §4.6.1: «ответ на основе отчёта».

**Категория агента:** RAG — ответ из отраслевого отчёта

## Запрос (canonical под категорию §4.6)

```
Что говорит OPEC MOMR о квотах добычи нефти на 2026 год? Дай цифры и ссылку на отчёт.
```

## Метаданные прогона

- **prod version:** v2.3.5+ (commit `c3c22f6`, PR #48 `fix/auto-enable-web-search`)
- **server:** `ws://186.246.2.190:8000/ws` (Docker image `nefteboros:dev`, image built 2026-05-11 12:48 UTC)
- **time (UTC):** 2026-05-11 13:03:02
- **duration:** 171.7s
- **client_message_id:** `examples_tz-01_1778504582`
- **sender_session_id:** `examples_session_tz-01_1778504582`
- **Langfuse trace:** [`fd71062e3176f3f3d85e0e9b19158f29`](https://cloud.langfuse.com/trace/fd71062e3176f3f3d85e0e9b19158f29)
- **tools/skills:** `rag_search`, `web_search`
- **observations:** 15
- **screenshot:** [`screenshots/scenario-tz-01.txt`](../screenshots/scenario-tz-01.txt) — CLI dump (см. ниже)

## Ответ агента (полный, как пришёл по WS)

В нашем корпусе (25 PDF) и в открытых tier1-источниках **OPEC Monthly Oil Market Report за 2026 год отсутствует**. Я не могу процитировать конкретную таблицу квот из MOMR — она не проиндексирована и не доступна в открытом доступе через веб-поиск.

Что есть в альтернативах:

### 1. IEA Oil 2025 — прогноз добычи OPEC (не квоты, а фактическая/ожидаемая)

Из проиндексированного отчёта `[IEA Oil 2025 — Analysis and forecast to 2030, p.135-136]`:

| Страна | Добыча 2024 (млн б/д) | 2025 | 2026 |
|---|---|---|---|
| Саудовская Аравия | 9,09 | 9,57 | 10,33 |
| Иран | 3,34 | 2,99 | 2,55 |
| Ирак | 4,31 | 4,27 | 4,45 |
| ОАЭ | 3,23 | 3,25 | 3,30 |
| Кувейт | 2,55 | 2,66 | 2,71 |
| Нигерия | 1,33 | 1,24 | 1,15 |
| Ливия | 1,07 | 1,16 | 0,99 |
| Алжир | 0,91 | 0,97 | 1,01 |
| Венесуэла | 0,88 | 0,77 | 0,64 |
| **Итого OPEC (сырая нефть)** | **27,24** | **27,43** | **27,66** |

*Примечание: это прогноз IEA, а не официальные квоты OPEC+.*

---

### 2. Последние решения OPEC+ по квотам (press releases, не MOMR)

| Дата | Решение | Источник |
|---|---|---|
| **5 апреля 2026** | Увеличение на **+206 тыс. б/д** (часть из 1,65 млн добровольных сокращений) | [OPEC.org, 5 апреля 2026](https://www.opec.org/pr-detail/1756597-5-april-2026.html) — opec.org, web |
| **3 мая 2026** | Принципиальное согласие на **+188 тыс. б/д** в июне, без ОАЭ | [OPEC+ set for another oil output quota hike despite Hormuz closure](https://www.reuters.com/business/energy/opec-agrees-principle-small-oil-output-quota-hike-without-uae-sources-say-2026-05-02/) — reuters.com, web |

Reuters отмечает, что увеличение **«в основном на бумаге»**, поскольку закрытие Ормуза мешает физической доставке.

---

### Итог

**OPEC MOMR за 2026 год в нашем корпусе отсутствует.** В открытом доступе он доступен только по подписке (OPEC Secretariat). Могу предложить данные из **IEA Oil 2025** (прогноз добычи выше) или **пресс-релизы OPEC+** по недавним пересмотрам квот (но это не полная таблица allocation).

Если у вас есть PDF MOMR — могу проиндексировать и дать точный ответ.

## RAG top-chunks (из trace)

- `ei_statistical_review_2025__0031` · score=0.585
- `ei_statistical_review_2025__0028` · score=0.578
- `iea_oil_2025__0028` · score=0.575
- `ei_statistical_review_2025__0034` · score=0.573
- `opec_woo_2025__0000` · score=0.572

## Web-источники (из trace)

- [Organization of the Petroleum Exporting Countries](https://www.opec.org/pr-detail/1756597-5-april-2026.html) — `www.opec.org`
- [Oil Market Report - April 2026 – Analysis - IEA](https://www.iea.org/reports/oil-market-report-april-2026) — `www.iea.org`
- [OPEC.org](https://www.opec.org/) — `www.opec.org`
- [OPEC+ set for another oil output quota hike despite Hormuz closure, sources say | Reuters](https://www.reuters.com/business/energy/opec-agrees-principle-small-oil-output-quota-hike-without-uae-sources-say-2026-05-02/) — `www.reuters.com`
- [Organization of the Petroleum Exporting Countries](https://www.opec.org/press-releases.html) — `www.opec.org`
- [Oil production in OPEC countries down to 36-year minimum — Bloomberg - Business & Economy - TASS](https://tass.com/economy/2127457) — `tass.com`
- [What Is OPEC, and How Does the Oil Cartel Influence Petroleum Prices? - The New York Times](https://www.nytimes.com/2026/04/28/business/opec-oil-cartel-prices.html) — `www.nytimes.com`

## Прошёл ли сценарий ожидания ТЗ?

В корпусе из 25 PDF (data/) нет конкретно OPEC MOMR — что агент явно сообщает в первом абзаце. Это **корректное поведение** под §2.1 ТЗ («не выдумывать данных»). Далее агент покрывает запрос через web с явной разметкой источников.
