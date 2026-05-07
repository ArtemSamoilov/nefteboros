# 2026-05-07 — Web tier classification: расширение по результатам аудита

**PR:** `fix/web-tier-classification-pass-2`
**Связано:** [ADR-0022](../adr/0022-web-search-brave.md), [changelog 2026-05-07-web-tier-subdomain-fix](2026-05-07-web-tier-subdomain-fix.md).

## Задача

Прогнал 10 разнообразных нефтегаз-запросов на live Brave (RU+EN, темы из ТЗ §2.3 / §4.6: forecast, sanctions, OPEC, СПГ, бюджет, Iran). 100 hits в сумме. Распределение **до** правок:

```
tier1: 19 (19%)
tier2:  8 (8%)
other: 73 (73%)
blacklist: 0 (0%)
```

73% `other` — главная проблема: tier-классификация недосписана для нашего use-case (политика sanctions, regulatory, отраслевые think tanks, региональная RU/UA пресса). Шум есть, но менее заметен.

## Решение — три кучи изменений

### TIER1 += 10 (приоритет — больше всего влияет на ranking)

| Хост | Категория |
|---|---|
| `minfin.gov.ru` | РФ-Минфин — нефтегаз-доходы РФ, налоговая формула |
| `state.gov` | US State — первоисточник санкций |
| `ofac.treasury.gov` | OFAC — официальный sanctions list |
| `bbc.com` | global tier1 news |
| `nytimes.com` | global tier1 news |
| `brookings.edu` | top think tank по policy |
| `atlanticcouncil.org` | top think tank по геополитике |
| `ieefa.org` | Institute for Energy Economics — energy economics research |
| `energyandcleanair.org` | CREA — tracking РФ нефтяных поставок (sanctions enforcement) |
| `lngjournal.com` | отраслевой LNG (важен для Новатэка/СПГ) |

### TIER2 += 19

EN general / regional: `aljazeera.com`, `politico.com`, `arabnews.com`, `asiatimes.com`, `fortune.com`, `newsweek.com`, `foxnews.com`, `finance.yahoo.com`.

RU independent: `meduza.io`, `themoscowtimes.com`, `svoboda.org`, `forbes.ru`.

RU отраслевой / business: `portnews.ru`, `abnews.ru`, `business-gazeta.ru`.

UA business: `forbes.ua`, `liga.net`, `epravda.com.ua`, `minfin.com.ua`.

### BLACKLIST += 9

Строго: yellow press / partner-promo / SEO-фермы. На таких источниках **не бывает** ценных отчётов или прогнозов:

| Хост | Категория |
|---|---|
| `life.ru` | yellow press |
| `discoveryalert.com.au` | junior stock promo |
| `seala.ru` | broker affiliate |
| `heygotrade.com` | broker affiliate |
| `litefinance.org` | forex broker marketing |
| `globalmarketnews.com` | generic feed без originality |
| `theglobalstatistics.com` | generic stats portal |
| `moneytimes.ru` | RU финансовый агрегатор без редакции |
| `investmint.ru` | то же |

## Что НЕ блокировали (с обоснованием)

- `english.pravda.ru`, `vz.ru` — pro-Kremlin biased, но это **позиция**, аналитику полезно видеть как indicator. Оставлены `other`.
- `pronedra.ru`, `urbc.ru`, `bcs-express.ru`, `sbercib.ru`, `alfabank.ru`, `tbank.ru`, `fomag.ru`, `bujet.ru`, `apecon.ru`, `profinance.ru` — RU отраслевая/брокерская пресса mid-tier; не yellow, но и недостаточно для tier2. Оставлены `other`.
- `coinbase.com` — off-topic, единичный hit; не плодит шум систематически.
- `tradingeconomics.com`, `investing.com` — market-data porталы; не источники news, но не yellow press. Оставлены `other`.

## Изменения

- [`nefteboros/search/tiers.py`](../../nefteboros/search/tiers.py): `_DEFAULT_TIER1` +10, `_DEFAULT_TIER2` +19, `_DEFAULT_BLACKLIST` +9. Группировка по категориям внутри set'ов с inline-комментариями.
- [`tests/test_search_tiers.py`](../../tests/test_search_tiers.py): `TestClassifyDefaults` +35 кейсов (все новые хосты), `TestSubdomainMatch` +9 кейсов (subdomains для новых хостов: `travel.state.gov`, `ru.themoscowtimes.com`, `biz.liga.net`, `news.life.ru` и т.д.).

## Тесты

`pytest tests/test_search_tiers.py`: **97 passed**.

## Ожидаемый ранжирующий эффект

Тот же 100-hit аудит прогнан с новой классификацией, ожидаем сдвиг ~30-40 hits из `other` в tier1/tier2 + ~3-5 hits в `blacklist` (исчезнут из выдачи). После deploy перепрогон того же набора запросов даст before/after для верификации.

## Deployment notes

Manifest skill'а не тронут — `content_hash` стабилен, **re-review не нужен**. На сервере: `git pull && systemctl restart nefteboros`. Smoke: повторить аудит-набор и сравнить распределение `tier1/tier2/other/blacklist` с baseline 19/8/73/0.
