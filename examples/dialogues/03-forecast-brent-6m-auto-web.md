# Диалог 3 — Прогноз Brent на 6 месяцев

**Категория:** Forecast — расчётный модуль (§4.6.4)

**Summary:** Запрос на прогноз 6m. Агент: classify_intent → forecast_call (модель `ou_regime`) → validate_citations → synthesize. После PR #48 web_search автоматически подключается рядом с forecast для актуализации текущего спота.

> ℹ Этот trace — **исторический snapshot v2.3.5+** (до fix'а PR #68). В ответе видна строка «галлюцинированные цитаты — метаданные pipeline не прошли внешнюю валидацию» — presentation-layer leak validate_citations. Fix вышел в **v2.3.6** (commit `582d921`), верификация в [диалоге 05](05-multi-tool-sanctions-forecast.md): 0/6 leak-маркеров на свежем v2.3.6 trace.

## Метаданные

- **trace_id:** `24d4dfe6124e9343e9eada8bb5bed47e` ([Langfuse](https://cloud.langfuse.com/trace/24d4dfe6124e9343e9eada8bb5bed47e))
- **session_id:** `chat:1`
- **timestamp (UTC):** 2026-05-11 11:08:12.313000+00:00
- **prod version:** v2.3.5+ (commit c3c22f6, PR #48)
- **tools/skills:** `analyst_query`, `classify_intent`, `forecast_call`, `validate_citations`, `web_search`

## Запрос

```
Сделай прогноз Brent на 6 месяцев
```

## Ответ агента

**Прогноз Brent (ICE front-month) на 6 месяцев**  
*Целевая дата: 8 ноября 2026. Точка фиксации модели: **$101,29 / баррель** (8 мая 2026). Текущий фьючерсный front-month (11 мая): ~$103,9–105,5.*

---

### Сводная таблица

| Сценарий | Точка, USD/барр | CI 80%, USD/барр | CI 95%, USD/барр |
|---|---|---|---|
| **Bear** (деэскалация) | **78,73** | 68,69 – 88,77 | 63,38 – 94,08 |
| **Base** (shock equilibrium) | **101,66** | 86,51 – 116,81 | 78,50 – 124,82 |
| **Bull** (эскалация) | **114,16** | 91,82 – 136,51 | 80,00 – 148,32 |

`[Forecast: ou_regime, scenario=bear, CI 80/95%]`  
`[Forecast: ou_regime, scenario=base, CI 80/95%]`  
`[Forecast: ou_regime, scenario=bull, CI 80/95%]`

---

### Методология и параметры модели

Использован **`ou_regime`** (regime-conditioned Ornstein–Uhlenbeck, ADR-0024). Запрошенный ранее **SARIMAX** в текущей конфигурации для горизонта 6м недоступен без изменения backend'а.

| Параметр | Bear | Base | Bull |
|---|---|---|---|
| Долгосрочное среднее μ₀ | $71,75 | $100,45 | $123,00 |
| Скорость реверсии θ | 3,0 /год | 2,0 /год | 1,5 /год |
| Half-life | ~2,8 мес | ~4,2 мес | ~5,5 мес |
| Волатильность σ | 25 %/год | 25 %/год | 30 %/год |

---

### Интерпретация сценариев

**Bear $78,73** — деэскалация: возобновление трафика через Hormuz, иранский экспорт восстанавливается к ~1,0–1,2 мбд, OPEC+ наращивает добычу без помех. Mean-reversion к pre-shock среднему ускорена (θ = 3), crisis premium выветривается к ноябрю.

**Base $101,66** — флет к споту ($101,29). Сохраняется частичная блокада Hormuz (−3 мбд), иранский экспорт зажат до ~0,4 мбд, OPEC+ unwind +206 тыс. бд/мес, price cap на РФ-нефть $47,60 (G7) / $44,10 (ЕС), китайский спрос +0,198 мбд г/г. Модель считает текущий premium устоявшимся.

**Bull $114,16** — эскалация: полная блокада Hormuz, иранский экспорт падает ближе к нулю, OPEC+ не компенсирует дефицит. Повышенная волатильность (σ = 30 %) и медленная реверсия (θ = 1,5) расширяют CI. Верхняя граница CI 95% ($148,32) — условный tail risk.

---

### Внешний бенчмарк: EIA STEO (10 мая 2026)

EIA ожидает **среднегодовой Brent в 2027 году на уровне ~$76 / баррель** при условии постепенного возобновления потоков через Hormuz `[Short-Term Energy Outlook - U.S. Energy Information Administration (EIA)](https://www.eia.gov/outlooks/steo/report/global_oil.php) — eia.gov, web`. Это не противоречит bear-сценарию $78,73 на ноябрь 2026: годовое среднее $76 подразумевает более глубокую коррекцию во II полугодии 2026.

---

### Ключевые оговорки

1. **Backtest недоступен** (`backtest_summary: null`) — невозможно сравнить точность с random walk или проверить фактическое покрытие доверительных интервалов.
2. **Валидационное предупреждение системы:** внутренние теги модели (`[Forecast: ou_regime, ...]`) помечены как потенциально «галлюцинированные цитаты» — метаданные pipeline не прошли внешнюю валидацию.
3. **Методология:** использован `ou_regime`, не SARIMAX; прямой вызов SARIMAX в текущей конфигурации недоступен.
4. **Геополитический overlay не подключён** — сценарийные драйверы — это параметризация внутренней модели, а не ручной cross-check с отчётами OPEC/IEA/CRS.

## Веб-источники (выдача brave-api)

- [Short-Term Energy Outlook - U.S. Energy Information Administration (EIA)](https://www.eia.gov/outlooks/steo/report/global_oil.php) — `www.eia.gov`
- [Oil Price Forecast 2026 | Crude Oil Price Predictions & Analysis](https://www.oilpriceapi.com/oil-price-forecast) — `www.oilpriceapi.com`
- [Oil Price Forecast 2026: What Brent and WTI Could Do This Year](https://financialdeepdive.com/energy/oil/oil-price-forecast-2026) — `financialdeepdive.com`
- [Market Prices Significant Chance of Oil Topping $125 in 2026 | Octagon AI](https://octagonai.co/news/wti-oil-price-forecast-2026/) — `octagonai.co`
- [Current price of oil as of May 4, 2026 | Fortune](https://fortune.com/article/price-of-oil-05-04-2026/) — `fortune.com`

## Что показывает

Виден полный pipeline §2.5 ТЗ: классификатор → расчётный модуль с интервалом доверия → синтез. Дополнительно — auto-enable web_search для подтягивания актуального spot anchor (фикс PR #48). Validate_citations отметило hallucinated citation предупреждением — это рабочий guard-rail.
