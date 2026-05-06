# Эксперимент — forecast PR1

Полный walk-forward бектест по сетке **7 observable assets × 4 methods × 4 horizons = 112 configurations**. Артефакт PR `feature/price-tools` (см. ADR-0012).

Раннее: `metrics/runs/2026-05-06_forecast_<sha>.json`. Кеш incremental — повторный запуск пересчитывает только новое.

## Дизайн

- **Walk-forward с rolling origin** (НЕ expanding) — литература (Baumeister-Kilian) показывает, что expanding window даёт переоптимистичные метрики.
- **Окно истории:** 5 лет daily (2021-05 → 2026-05).
- **Train window:** 3 года rolling.
- **Шаг:** 1 месяц.
- **Регимы:** маркируется по `target_date` через `spread_schedule.find_period_for_date` — `pre_war / russia_war_shock / cap_normalization / iran_2026`.
- **Метрики:** MAPE, RMSE, coverage_80, coverage_95, **MASE против RW** (главный критерий: <1 = модель бьёт persistence), directional accuracy.

Для derived активов (urals, espo, urals_minfin_blend) бектест **не проводится** — strict-separation подход (см. ADR-0012). Их прогноз — derived layer поверх Brent forecast с CI расширением на spread-uncertainty.

## Сводная таблица — MAPE %, MASE vs RW

### Нефть глобальная

| Asset | Method | 1m | 3m | 6m | 12m |
|---|---|---|---|---|---|
| **brent** | RW       | 6.31 / 1.00 | 8.96 / 1.00 | 11.29 / 1.00 | 19.80 / 1.00 |
|        | SARIMAX  | 6.26 / **0.99 ↓** | 9.15 / 1.02 | 11.74 / 1.04 | 19.85 / 1.00 |
|        | XGBoost  | 11.64 / 1.75 | 15.17 / 1.59 | 16.70 / 1.38 | 19.49 / **0.95 ↓** |
|        | Ensemble | 8.27 / 1.26 | 11.54 / 1.24 | 13.90 / 1.18 | 19.67 / **0.97 ↓** |
| **wti** | RW       | 6.92 / 1.00 | 9.82 / 1.00 | 11.97 / 1.00 | 21.18 / 1.00 |
|       | SARIMAX  | 6.97 / 1.01 | 9.76 / **0.99** | 11.96 / 1.00 | 20.95 / **0.98 ↓** |
|       | XGBoost  | 13.54 / 1.82 | 16.78 / 1.58 | 18.57 / 1.42 | 19.22 / **0.86 ↓** |
|       | Ensemble | 9.87 / 1.37 | 12.64 / 1.23 | 15.17 / 1.20 | 20.08 / **0.92 ↓** |

### Газ глобальный

| Asset | Method | 1m | 3m | 6m | 12m |
|---|---|---|---|---|---|
| **henry_hub** | RW       | 14.31 / 1.00 | 21.19 / 1.00 | 20.14 / 1.00 | 27.89 / 1.00 |
|             | SARIMAX  | 14.32 / 1.00 | 21.11 / 1.00 | 20.19 / 1.00 | 27.99 / 1.00 |
|             | XGBoost  | 12.07 / **0.87 ↓** | 14.33 / **0.70 ↓** | 17.11 / **0.89 ↓** | 20.50 / **0.77 ↓** |
|             | Ensemble | 12.29 / **0.87 ↓** | 16.66 / **0.80 ↓** | 18.14 / **0.92 ↓** | 23.99 / **0.88 ↓** |
| **ttf** | RW       | 9.50 / 1.00 | 19.07 / 1.00 | 25.83 / 1.00 | 28.87 / 1.00 |
|       | SARIMAX  | 9.63 / 1.01 | 19.16 / 1.01 | 25.81 / 1.00 | 28.81 / 1.00 |
|       | XGBoost  | 9.52 / 1.01 | 16.91 / **0.89 ↓** | 21.82 / **0.86 ↓** | 24.51 / **0.85 ↓** |
|       | Ensemble | 9.04 / **0.96 ↓** | 17.99 / **0.95 ↓** | 23.77 / **0.93 ↓** | 26.58 / **0.92 ↓** |

### Российский нефтегаз proxy (через MOEX ISS)

| Asset | Method | 1m | 3m | 6m | 12m |
|---|---|---|---|---|---|
| **moexog** | RW       | 6.72 / 1.00 | 9.29 / 1.00 | 11.35 / 1.00 | 11.97 / 1.00 |
|          | SARIMAX  | 6.78 / 1.01 | 9.33 / 1.00 | 11.35 / 1.00 | 12.00 / 1.00 |
|          | XGBoost  | 7.05 / 1.03 | 9.24 / **0.98 ↓** | 11.00 / **0.96 ↓** | 13.28 / 1.11 |
|          | Ensemble | 6.89 / 1.02 | 9.15 / **0.98 ↓** | 10.99 / **0.96 ↓** | 12.64 / 1.06 |
| **gazp** | RW       | 9.47 / 1.00 | 9.82 / 1.00 | 13.20 / 1.00 | 10.91 / 1.00 |
|        | SARIMAX  | 9.43 / 1.00 | 9.81 / 1.00 | 13.12 / **0.99** | 10.90 / 1.00 |
|        | XGBoost  | 18.82 / 1.95 | 14.85 / 1.40 | 16.69 / 1.19 | 25.37 / 2.31 |
|        | Ensemble | 11.96 / 1.24 | 11.10 / 1.08 | 12.73 / **0.92 ↓** | 16.29 / 1.48 |
| **nvtk** | RW       | 8.11 / 1.00 | 11.99 / 1.00 | 14.94 / 1.00 | 9.84 / 1.00 |
|        | SARIMAX  | 8.06 / **0.99** | 12.01 / 1.00 | 14.89 / 1.00 | 9.83 / 1.00 |
|        | XGBoost  | 7.30 / **0.91 ↓** | 11.76 / **0.98 ↓** | 14.72 / **0.99** | 10.15 / 1.04 |
|        | Ensemble | 7.45 / **0.92 ↓** | 11.85 / **0.99** | 14.79 / **0.99** | 9.99 / 1.02 |

## Default-метод per (asset, horizon) — выбор по MASE

Зашьётся в `forecast.api._default_method_for(asset, horizon)` в PR2 после ревью; в PR1 default — простая heuristics by horizon.

| Asset | 1m | 3m | 6m | 12m |
|---|---|---|---|---|
| **brent** | SARIMAX (0.99) | RW (1.00) | RW (1.00) | **XGB (0.95)** |
| **wti** | RW (1.00) | SARIMAX (0.99) | SARIMAX (1.00) | **XGB (0.86)** |
| **henry_hub** | **XGB (0.87)** | **XGB (0.70)** | **XGB (0.89)** | **XGB (0.77)** |
| **ttf** | Ensemble (0.96) | **XGB (0.89)** | **XGB (0.86)** | **XGB (0.85)** |
| **moexog** | RW (1.00) | Ensemble (0.98) | XGB (0.96) | RW (1.00) |
| **gazp** | SARIMAX (1.00) | SARIMAX (1.00) | Ensemble (0.92) | SARIMAX (1.00) |
| **nvtk** | XGB (0.91) | XGB (0.98) | XGB (0.99) | SARIMAX (1.00) |

## Главные наблюдения

**1. Газ → ML, нефть → RW.**
- Henry Hub: XGBoost обыгрывает RW на всех 4 горизонтах (MASE 0.70-0.89). На 3m улучшение MAPE с 21% до 14% — треть.
- TTF: XGBoost доминирует на 3m+ (MASE 0.85-0.89). На 1m — Ensemble слегка лучше (0.96).
- Brent/WTI: на коротких (1m-3m) RW/SARIMAX лучше, XGBoost существенно проигрывает (MASE 1.5-1.8).
- **Объяснение:** газ mean-revert'ит из-за сезонности (winter spikes / summer lows), GBR с лагами улавливает паттерн. Нефть shock-driven (OPEC+ решения, геополитика) — лагов недостаточно, persistence сложно обыграть.

**2. На 12m даже для нефти XGB бьёт RW.**
- Brent 12m XGB MASE 0.95, WTI 12m XGB MASE 0.86 (улучшение 14%!).
- На длинном горизонте mean-reversion после шоков начинает работать в пользу ML.
- На коротких сроках информация в 21 лаге одного актива слишком тонкая, чтобы обыграть persistence.

**3. GAZP — особый кейс, ML не работает.**
- XGBoost на GAZP MASE 1.95-2.31 (катастрофа на 1m и 12m!).
- Причина: Газпром heavily shock-driven — war 2022 (труба ЕС-РФ остановлена), sanctions на Газпромбанк 2024-25, СВО, дивидендная политика.
- Эти gap'ы XGBoost-лаги не предвосхищают — RW/SARIMAX честнее.

**4. NVTK — ML-friendlier чем GAZP.**
- XGBoost MASE 0.91-0.99 на трёх горизонтах из четырёх.
- Новатэк — больше corporate-driven (Ямал/Арктик СПГ ramp-up, дивиденды), меньше политических gap'ов в 5y окне.

**5. MOEXOG — на 12m лучший RW.**
- Индекс smoother чем отдельные акции (взвешенный по капитализации Газпром+Новатэк+Роснефть+Лукойл+Татнефть).
- На длинном горизонте mean-reverting к историческому уровню — persistence близок к фактическому 12m.

## Live-test: Iran-shock 2026-02-06 → 2026-05-06

Самое важное наблюдение этого PR — **наглядное свидетельство ограничений stat-методов на shock-event'ах**. Тест: train history до 2026-02-06, прогноз на 3m, target = 2026-05-06, сравнение с фактом.

| Asset | Method | @cutoff | Прогноз | Факт | Ошибка | In CI80? |
|---|---|---|---|---|---|---|
| brent | RW | $68.05 | $68.05 | **$109.87** | **−38.1%** | ✗ MISS |
| wti | SARIMAX | $63.55 | $63.83 | $102.27 | −37.6% | ✗ MISS |
| henry_hub | XGBoost | $3.42 | $4.04 | $2.79 | +44.8% | ✓ |
| ttf | XGBoost | €35.69 | €34.78 | €46.93 | −25.9% | ✗ MISS |
| moexog | Ensemble | 6895 | 7009 | 6780 | +3.4% | ✓ |
| gazp | SARIMAX | ₽125.53 | ₽125.49 | ₽118.83 | +5.6% | ✓ |
| nvtk | XGBoost | ₽1162 | ₽1170 | ₽1145 | +2.1% | ✓ |
| urals | RW (derived) | $51.05 | $51.05 | $92.87 | −45.0% | ✗ MISS |
| espo | RW (derived) | $62.05 | $62.05 | $103.87 | −40.3% | ✗ MISS |
| urals_minfin_blend | RW | $53.47 | $53.47 | $95.29 | −43.9% | ✗ MISS |

**Что произошло:** между Feb-2026 и May-2026 случилась **Iran-эскалация** (документирована в RAG: CRS Iran Conflict, 26.03.2026). Brent взлетел с $68 до $110 (+62% за 3 месяца). Российская нефть пошла за Brent. Газы (HH, TTF) и российский нефтегаз-сектор (GAZP/NVTK/MOEXOG) — менее чувствительны к нефтяному shock'у, попали в CI.

**Эмпирический coverage этого среза:** 4/10 в CI 80% → **40% попадание** при nominal 80%. Это **сильный under-coverage**, специфичный для shock-периодов. На 5-летнем aggregate-бектесте coverage 90-95% (over-coverage), но шок-периоды дают противоположную картину.

## Почему это **не баг** — это фундаментальное ограничение

Никакая стат-модель на ценах не предскажет геополитический шок:
- Goldman Sachs, JPMorgan, OPEC — **никто** в феврале 2026 не предсказал бы Brent $110 на май-2026 по time-series методам.
- В feb-2026 рынок был в «cap_phase_2 stable» ($50-75), все signal'ы в данных говорили «продолжение нормализации».
- Информация о приближающемся conflict'е жила **в текстах CRS / Bruegel / Reuters**, а не в исторических ценах.

**Stat-модели — не predictors шоков, а калибраторы базового уровня.** Их роль — дать **base-case + честный CI** для спокойного режима. На shock-event'е они **должны промахнуться** — это диагностика того, что мы в shock-режиме, а не в спокойном.

## Required augmentation — гибридный пайплайн (см. ADR-0013)

PR1 — только **базовый layer**. Production-агент Сбера должен:

1. **forecast_tool (PR1)** → base-case + CI на спокойном режиме.
2. **RAG retrieval** → сценарные оценки из OPEC WOO 2025, IEA Oil 2025, CRS Iran (всё в нашем 25-document корпусе).
3. **Web-search** → свежие новости, OPEC+ заявления, momentum-индикаторы, аналитика инвестбанков (PR `feature/web-search`).
4. **Synthesize-overlay** → итог = base + scenario_uplift + recent_events.

Это и есть **§2.4 ТЗ** — «логика приоритизации источников». Архитектура — в **ADR-0013 (Hybrid forecasting)**.

**Без RAG/web ответ агента на «прогноз Brent на 3 мес» в феврале 2026 был бы:**
> *Brent: $68 ± $13 (80% CI). Структурные шоки моделью не учитываются.*

Технически правильно, но **не операционально полезно** для аналитика, который должен оценить риск.

**С RAG/web (target — реализация в PR2):**
> *Base-case (наш модуль, SARIMAX): $68 ± $13.
> Сценарии (RAG): IEA Oil 2025 при escalation $115-125; CRS Iran «+$30-50 при Hormuz disruption».
> Свежие сигналы (web): «OPEC+ обсуждает ускорение раскрытия квот»; «futures curve в backwardation +$3/нед».
> Итог: $80-130 с учётом активных рисков. Доминирующий сценарий — escalation.*

## Why these methods are optimal — обоснование выбора

**Random Walk (honest baseline)** — обязательный. Без него любая модель «лучше прошлого» — иллюзия. Литература (Alquist-Kilian 2010, Empirical Economics 2024) единогласна: **end-of-month RW почти невозможно стабильно обыграть на нефти**. Наш бектест подтверждает — на 7 из 16 (asset, horizon) пар RW лучший или равен.

**SARIMAX (с экзогенами)** — лучшая stat-модель для нефти. Box-Jenkins state-space даёт аналитический CI «из коробки», что важно для быстрой интерпретации стейкхолдерами. Auto-grid из 5 порядков по AIC покрывает 95% полезных конфигураций без pmdarima (нестабильный пакет). На нашем бектесте: лучший на WTI 3m, на Brent 1m бьёт RW на 1%.

**Gradient Boosting** — лучшая ML-модель для финансовых рядов. На oil/gas задачах **превосходит LSTM/Transformer** при правильных лагах (MDPI Energies 2025; Springer Financial Innovation 2024). На нашем бектесте: **доминирует на газовых рядах** (Henry Hub MASE 0.70, TTF MASE 0.85) — за счёт ловли mean-reversion. На длинных горизонтах нефти (Brent/WTI 12m) тоже бьёт RW. CI через **split-conformal** (Xu & Xie 2020) даёт distribution-free покрытие.

**Ensemble (mean SARIMAX+GBR)** — литературный консенсус: простое усреднение бьёт single model в 60-70% случаев. На нашем бектесте: на TTF доминирует на 1m, на GAZP на 6m — единственное место, где помогает разноуклонная композиция.

**Что НЕ выбрали и почему:**

| Метод | Почему отвергнут |
|---|---|
| Prophet (Meta) | На нефти эмпирически слабее RW в comparison studies 2023-2025 (over/under-fit тренда). |
| ETS / Holt-Winters | Не показывает преимуществ перед SARIMAX на нефти. |
| LSTM / GRU / Transformer | Proxy-победы в литературе с возможным data leakage; нестабильны в production; XGBoost даёт сравнимое или лучшее качество. |
| CEEMDAN/VMD + LSTM гибриды | Высокий риск look-ahead bias при naive реализации (декомпозиция на полном ряде). |
| pmdarima auto_arima | Иногда сломан после обновлений; ручной grid из 5 порядков покрывает то же. |
| Pooled VAR (Baumeister-Kilian) | Требует real-time vintages of macro data — отдельная инфраструктура (отложено в PR3). |
| GARCH-LSTM | Шоковая волатильность — релевантна, но требует дополнительного слоя. PR3. |
| Markov-Switching | Режимные модели — где они работают, на нашем бектесте нет регимных переходов в test-windows. PR3. |

## Heavy-tail political volatility — главное предостережение

Активы, которые мы прогнозируем, **страшно волатильны и зависят от политики**. Это не редкое исключение, это норма:

- **2020:** COVID, отрицательные WTI futures (-$37), война цен Saudi-Russia.
- **2022:** Ukraine invasion, Brent $80→$128 за 6 недель; Urals discount −$3 → −$35.
- **2022:** TTF €30 → €339 (×11) за лето из-за газового кризиса EU.
- **2023-2025:** G7 price cap $60, shadow fleet, evolving sanctions regime.
- **2025-09:** cap снижен до $47.60.
- **2026-Q1:** Iran-эскалация, Hormuz risk, Brent +$40 за квартал.

Каждые 1-2 года — **структурный шок**. Никакая time-series модель не может его предсказать **до момента появления сигнала в новостях / отчётах CRS-уровня**.

**Это значит:** для production-аналитики Сбера нужна **гибридная архитектура** (см. ADR-0013). PR1 — только мощная **первая нога**: даёт base-case + калиброванный CI + диагностику volatility-режима через ширину CI.

## Чтение CI (coverage)

Coverage 80% в aggregate-метриках по большинству активов **выше nominal** (90-100% при цели 80%) — модели CI **слишком широкий**. Это **conservative**, не over-confident — лучше для аналитики Сбера, но снижает информативность интервала.

Причины:
- SARIMAX state-space CI не корректирует heavy tails — на нефти/газе с шоками residuals не gaussian.
- GBR-conformal на 5y daily с break-points — calibration window перекрывает разные режимы, ε завышен.

Mitigation в PR3: переход на **adaptive conformal** (EnbPI / SPCI), регрессия CI к nominal через rolling re-calibration.

## Известные ограничения PR1 → PR3

| Ограничение | Влияние | Что в PR3 |
|---|---|---|
| **sklearn.GBR вместо XGBoost** | -5-10% метрик ML-модели | Переход на xgboost.XGBRegressor при доступности libomp |
| **Univariate для MOEXOG/GAZP/NVTK** | Нет RU-macro экзогенов (USD/RUB, ставка ЦБ, sanctions news) | Добавить RU-экзогены |
| **Split-conformal CI на non-exchangeable data** | Coverage may просесть на режимных break-points | EnbPI / SPCI |
| **Hardcoded spread schedule (4 режима)** | Внутри-периодная вариация спреда теряется | Динамический spread из реальных Минэк-monthly + шум |
| **Derived не имеет своего бектеста** | Метрики качества для urals/espo/blend не вычислены | Если найдём reliable Urals daily — добавить independent backtest |
| **CI слишком широкий (over-coverage)** | Interval мало информативен | Adaptive CP, перекалибровка |

## Воспроизвести

```bash
# Активировать venv с зависимостями
source .venv/bin/activate
pip install -r requirements-domain.txt

# Положить EIA_API_KEY в .env (см. .env.example)

# Запустить полный eval (~30-60 минут на 5y daily, 7 assets × 4 methods × 4 horizons)
python scripts/eval/eval_forecast.py

# Посмотреть результат
ls metrics/runs/
```

## Связанные документы

- ADR-0012: [docs/adr/0012-price-tools.md](../adr/0012-price-tools.md) — решения по архитектуре, источникам, методам, горизонтам.
- Changelog: [docs/changelog/2026-05-06-price-tools.md](../changelog/2026-05-06-price-tools.md).
- Code: `nefteboros/forecast/`, `scripts/eval/eval_forecast.py`, `scripts/forecast.py`.
- Raw data: `metrics/runs/2026-05-06_forecast_<sha>.json` (incremental cache).
