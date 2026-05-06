# ADR-0012 — Price tools: источники, методы, горизонты прогнозирования

- **Дата:** 2026-05-06
- **Статус:** Принято
- **Контекст:** PR `feature/price-tools` (PR1 из 2-3 в forecast-цепочке)
- **Связано:** ADR-0001 (форк Ouroboros), ADR-0007 (LLM-провайдеры), ADR-0009 (RAG-корпус). Будущее: ADR-0013 (Ouroboros forecast-skill), ADR-0014 (advanced models — Baumeister-Kilian / GARCH / Markov-Switching).

## Контекст и проблема

ТЗ §2.5 требует расчётный модуль прогнозирования цен на нефть: ≥2 метода, доверительный интервал, краткая интерпретация, агент сам решает когда вызывать. Минимум — Brent.

Расширение scope (Артём, 2026-05-06): кроме Brent — **российские сорта (Urals, ESPO)** под фокус на госбанк-клиента (Сбер кредитует Роснефть/Газпром/Лукойл/Новатэк/Татнефть, бюджет РФ зависит от нефтегаздоходов на ~30%) и **газ как равноправный поток** (российский СПГ Новатэка, экспорт Газпрома, внутрироссийский газовый рынок СПбМТСБ).

Прогноз в роли «аналитика» != прогноз в роли «дей-трейдера». Горизонты — **месяц/квартал/полугодие/год**, не «завтра». На 1-7 дней нефть ведёт себя как mean-reverting шумовой процесс, ML/stat-методы там почти не бьют persistence; на ≥18 мес точечный прогноз бесполезен (литература единогласна) — нужны сценарии.

Research (см. Sources внизу) выявил несколько неочевидных требований:

1. **End-of-month random walk** на нефти — настолько сильный benchmark, что почти ни один отдельный метод не бьёт его стабильно. Без RW в наборе модель «лучше прошлого» — иллюзия (Alquist-Kilian, Empirical Economics 2024).
2. **Forecast combination** (простое усреднение) бьёт single model в 60-70% случаев на oil-рядах.
3. **Prophet** на нефти **слабее** RW — академически подтверждено, не включаем.
4. **XGBoost** на финансовых рядах часто превосходит LSTM/Transformer при правильных лагах + macro-фичах. Конформал-предсказание — distribution-free CI, держит nominal coverage даже при шоках.
5. **5-летнее окно (2021-2026)** содержит 2-3 структурных слома (COVID-tail, Ukraine 2022, sanctions cap, Iran 2026) — walk-forward с rolling origin обязателен; expanding window даёт переоптимистичные метрики.
6. **Urals = Brent − spread.** Urals-Brent spread в 2022 ушёл с −$3 до −$35 за 6 недель (sanctions cap). Прямая модель Urals на этом окне — артефактная; раздельная модель Brent (главное) и spread (со своей режимной природой) — стандартный подход.

## Решение

Реализуем расчётный модуль `nefteboros/forecast/` со следующими параметрами.

### Активы (11 P0)

| # | Группа | Asset ID | Daily-источник | Frequency |
|---|---|---|---|---|
| 1 | Глобальная нефть | `brent` | yfinance `BZ=F` + EIA `RBRTE.D` | daily |
| 2 |  | `wti` | yfinance `CL=F` + EIA `RWTC.D` | daily |
| 3 | Российская нефть | `urals` | investing.com (2021-05 → 2025-02) + Brent + Минэк-spread proxy (2025-02 → наст.) | daily |
| 4 |  | `espo` | то же + Минэк ESPO-spread monthly | daily |
| 5 |  | `urals_minfin_blend` | derived = 0.78 × `urals` + 0.22 × `espo` | daily |
| 6 | Глобальный газ | `henry_hub` | yfinance `NG=F` + EIA `RNGWHHD.D` | daily |
| 7 |  | `ttf` | yfinance `TTF=F` | daily |
| 8 |  | `jkm` | investing.com (JKM Platts) | daily |
| 9 | Российский нефтегаз proxy | `moexog` | MOEX ISS API (Oil & Gas Index) | daily |
| 10 |  | `gazp` | MOEX ISS API (Газпром, TQBR) | daily |
| 11 |  | `nvtk` | MOEX ISS API (Новатэк, TQBR) | daily |

**P1 (если время в PR1):** OPEC reference basket (`opec.org` XML).
**P2 (post-MVP):** UK NBP, Новатэк-СПГ дисконт к JKM, SPIMEX-индекс через коммерческий канал (sales@spimex.com / платный API).

### Discovery — что не работает в открытых источниках

Зафиксировано во время research-фазы для будущих авторов:

| Источник | Проверено | Статус | Замена |
|---|---|---|---|
| **СПбМТСБ daily** | spimex.com | UI-показывает данные с 2015, но **download закрыт за коммерческим каналом** (sales@spimex.com / платный API) | `moexog` через MOEX ISS как рыночный proxy сектора |
| **CBR gas.xls** (экспорт газа РФ monthly) | cbr.ru | Файл живой, но **обновление прекращено 25.03.2022**, последняя точка IV кв. 2021 | `gazp` + `nvtk` через MOEX ISS как proxy экспортёров |
| **ФТС / Росстат** | customs.gov.ru, rosstat.gov.ru | timeout / SSL EOF — IP-блок не-РФ | — |
| **yfinance Russian tickers** (GAZP.ME, NVTK.ME...) | yfinance | Yahoo прекратил обновлять MOEX-фид в **мае 2022** (sanctions cutoff) | прямой MOEX ISS API |
| **Investing.com по Urals** | investing.com | Фид остановлен ≈ февраль 2025 | гибрид: 4 года clean + Brent+Минэк-spread proxy для последних ~14 мес |
| **Investing.com по ESPO** | investing.com | HTTP 404 — сорта нет на платформе | то же что и Urals |

**Вывод для будущих PR:** при работе с российскими финансовыми рядами **MOEX ISS** (`iss.moex.com`) — первый источник, не последний. Публичный REST API биржи отдаёт котировки, индексы, history без VPN и без auth.

### Методы прогноза

В каждом запуске обучаются и сравниваются **четыре метода**:

| Метод | Роль | CI | Применимость |
|---|---|---|---|
| **End-of-month Random Walk** | honest baseline; зеркало для остальных | empirical residual | Все активы |
| **SARIMAX** с экзогенами (EIA crude inventories, DXY, futures front-curve) | интерпретируемая stat-модель | analytical (Box-Jenkins) | Все активы; для daily — daily ARIMA, для monthly — monthly ARIMA |
| **XGBoost** на лагах цены + лагах macro + futures curve features | ML | split-conformal с rolling calibration window | Только daily (на monthly = 60 точек слишком мало для split-conformal) |
| **Ensemble = mean(SARIMAX, XGBoost)** | production default | bootstrap по компонентам | Только daily; на monthly ensemble = mean(RW, SARIMAX) |

**Вычеркнуто из PR1 с обоснованием:**
- **Prophet** — на нефти эмпирически слабее RW (over/underfit тренда). NeuralProphet — узкая ниша, не оправдан.
- **ETS / Holt-Winters** — без преимуществ перед SARIMAX на нефти.
- **GARCH** — даёт волатильность, не цену. Полезен для CI в шоковых режимах, отложен в PR3.
- **LSTM / Transformer** — proxy-победы в литературе, нестабильны в production, требуют много данных. Отложены.
- **CEEMDAN / VMD + LSTM гибриды** — высокий риск data leakage при наивной реализации (декомпозиция на полном ряде → look-ahead bias).

### Горизонты прогноза

API:

```python
from nefteboros.forecast import forecast, Horizon, Asset

result = forecast(asset="brent", horizon="3m")
# result.point: float
# result.ci_80: tuple[float, float]
# result.ci_95: tuple[float, float]
# result.method: str (выбран по бектесту)
# result.interpretation: str (текст для агента)
# result.metadata: {asset, horizon, model, backtest_metrics, history_window, ...}
```

| Horizon | Поддержка | Default-модель | Поведение |
|---|---|---|---|
| `1m` | ✅ | RW либо SARIMAX (по бектесту) | Узкий CI, но добавленная ценность над persistence минимальна |
| `3m` | ✅ | Ensemble (типично) | Стат-модели начинают обыгрывать RW, ML добавляет качества |
| `6m` | ✅ | Ensemble | Структурные факторы доминируют, CI расширяется |
| `12m` | ✅ + warning | Ensemble + явный disclaimer | Точечная оценка ненадёжна, рекомендация на сценарии в RAG |
| `≥18m` | ❌ | — | Отказ + перенаправление на сценарии: WOO 2025, IEA Oil 2025, ИНЭИ — все в RAG |
| `1d` / `7d` | ❌ | — | Не наша область (дей-трейдинг); RW + futures всё равно бьют |

Default-модель **выбирается по бектесту** (см. ниже) и фиксируется в `docs/experiments/forecast.md` как таблица `asset × horizon → model`.

### Доверительные интервалы

- **RW:** empirical residual (квантили исторических остатков на rolling-окне).
- **SARIMAX:** analytical (Kalman filter в `statsmodels.tsa.statespace`). Корректируем на heavy-tails через t-distribution.
- **XGBoost:** **split-conformal prediction** с rolling calibration window. Calibration set ≈ 20% самого свежего трейн-окна; `α=0.20` для 80% CI, `α=0.05` для 95% CI. Сохраняет nominal coverage при структурных сломах (distribution-free).
- **Ensemble:** simple bootstrap — sample из {SARIMAX_residual, XGB_conformal_residual}, перцентили.

CI масштабируется с √horizon (volatility scaling) — стандартный подход для multi-step forecasts.

### Бектест

**Walk-forward с rolling origin:**
- Окно истории: **5 лет** daily (для monthly активов = 5 лет / 60 точек, отдельный режим).
- Train window: rolling 3 года → forecast horizon → re-fit при каждом шаге.
- Step: 1 месяц.
- Re-fit модели на каждом шаге (без in-sample leakage).

**Метрики:**
- **MAPE** — точечная точность.
- **RMSE** — точечная точность с штрафом за большие ошибки.
- **Coverage 80% / 95%** — эмпирическое покрытие CI (доля попаданий факта в интервал). Должно быть ≈ nominal; систематическое under-coverage = модель оптимистична.
- **MASE relative to RW** — главный критерий. <1 = модель обыгрывает persistence; ≥1 = модель не нужна, RW честнее.
- **Directional accuracy** — доля верно предсказанных знаков delta за horizon.

**Сегментация по режимам** (отдельные метрики для каждого, плюс aggregate):
- `pre_2022` (2021-01 — 2022-02)
- `russia_war_shock` (2022-02 — 2022-12)
- `cap_normalization` (2023-01 — 2025-12)
- `iran_2026` (2026-01 — наст.)

Это даёт честное представление: модель может быть хороша в спокойном режиме и слаба в шоковом — нужно знать обе цифры, не один аггрегат.

### Архитектура per asset (после self-review-bombing)

**Принцип strict separation:** модели **обучаются только на наблюдаемых рядах**. Российские нефтесорта (urals, espo) и blend получаются как **derived layer** поверх базового прогноза Brent — без собственных моделей. Это исключает circular reasoning, при котором модель «учится на собственной формуле reconstruction».

**MOEXOG/GAZP/NVTK — univariate без нефтяных экзогенов**, потому что это акции (драйверы — RUB/USD, ставка ЦБ, sanctions news), и подача EIA-inventories/DXY как фичей будет methodological mistake. RU-macro экзогены — отложены в PR3.

```
# Observable, обучаются с экзогенами (EIA inventories, DXY, futures curve):
brent              → RW, SARIMAX, XGBoost, Ensemble
wti                → то же (corr с Brent ~0.95, но избегаем каскадирования ошибок)
henry_hub          → то же, log-transform
ttf                → то же, log-transform (экстремумы 2022 ×10 от нормы)

# Observable, univariate (без нефтяных экзогенов — это акции, не commodity):
moexog             → RW, ARIMA, XGBoost, Ensemble — без exog
gazp               → то же
nvtk               → то же

# Derived — НЕ обучаются. Получаются преобразованием Brent-прогноза:
urals              → Brent_forecast(method) − spread_curr(date_target)        [spread_schedule.py]
espo               → Brent_forecast(method) − spread_espo_curr(date_target)
urals_minfin_blend → piecewise:
                       date < 2025-01: = urals(method)                         [до этого Минфин считал НДПИ только по Urals]
                       date >= 2025-01: = 0.78 × urals(method) + 0.22 × espo(method)
```

**CI для derived-активов** расширяется на spread-uncertainty. В `spread_schedule.py` каждый период хранится как `(low, mid, high)` — например, для war_shock 2022 это `($25, $30, $35)`. Доверительный интервал derived-актива:
- `CI(urals) = CI(Brent_forecast) ⊕ uniform([spread_low, spread_high])` — convolution.
- Для blend — двойная convolution.
- Эффект: CI на urals/espo шире, чем на brent. **Это честный знак** — не претендуем на наблюдаемую точность по Urals.

**Почему derived-без-обучения для Urals/ESPO** — после research-фазы выяснилось, что прямых daily-цен Urals/ESPO в открытых источниках на 5-летнее окно нет (investing.com обрывается в Feb 2025, ESPO там вообще 404, остальные free-источники либо за платной стеной, либо за JS-чартом). Если бы мы реконструировали ряд через `Brent + hardcoded spread` и потом обучали на нём SARIMAX/XGBoost — модели **схватили бы собственную формулу** (spread piecewise constant в пределах квартала) и показывали бы «отличный» MAPE, не имеющий отношения к реальной способности предсказывать Urals. Поэтому Urals/ESPO/blend — **derived поверх Brent-прогноза**, без собственных моделей. Бектест считается **на наблюдаемом Brent** (где он осмысленный), для derived-активов в эксперименте указывается — qualifier «derived from brent forecast + spread_uncertainty»; метрики не вычисляются как самостоятельные.

**Baseline для спреда — Brent-spot vs Brent-futures.** В spread_schedule.py я фиксирую spread как `Brent_yfinance_futures − Urals_<period>`, потому что live-прогноз идёт через yfinance-futures (свежесть). EIA-spot (RBRTE.D) с задержкой 1 неделя — verification-only. При наблюдаемой систематической ошибке прогноза Urals — пересмотрим baseline на EIA-spot.

**Почему WTI напрямую, а не через спред** — WTI — глобальный benchmark, не российская история; каскадирование (Brent → WTI) добавит ошибки без выгоды.

## Конфигурация

### Env vars (новые)

```
# EIA — фундаментальные ряды и spot-цены (бесплатный ключ на eia.gov/opendata/register.php)
EIA_API_KEY=

# Investing.com скрейпер
INVESTING_USER_AGENT=Mozilla/5.0 (compatible; nefteboros-forecast/0.1)
INVESTING_RATE_LIMIT_SEC=2

# СПбМТСБ скрейпер
SPIMEX_BASE_URL=https://spimex.com/markets/gas/

# ЦБ РФ
CBR_GAS_EXPORT_URL=https://www.cbr.ru/vfs/statistics/credit_statistics/trade/gas.xls

# Forecast — общие настройки
FORECAST_HISTORY_YEARS=5
FORECAST_CACHE_DIR=datasets/forecast_cache
FORECAST_CACHE_TTL_HOURS=24
FORECAST_BACKTEST_TRAIN_WINDOW_YEARS=3
FORECAST_BACKTEST_STEP_MONTHS=1
FORECAST_DEFAULT_HORIZON=3m
```

### Layout каталогов

```
nefteboros/forecast/
├── __init__.py
├── api.py                  # forecast() entry point, Asset/Horizon enums
├── schema.py               # ForecastResult, BacktestResult, ConfidenceInterval (pydantic)
├── registry.py             # реестр assets с метаданными (источник, frequency, model overrides)
├── cache.py                # CSV-кеш с TTL
├── data/
│   ├── __init__.py
│   ├── yf.py               # yfinance fetcher
│   ├── eia.py              # EIA API client
│   ├── investing.py        # investing.com скрейпер (Urals/ESPO/JKM)
│   ├── spimex.py           # СПбМТСБ скрейпер
│   ├── cbr.py              # ЦБ РФ xls fetcher
│   └── exog.py             # экзогены для SARIMAX (DXY, EIA inventories)
├── models/
│   ├── __init__.py
│   ├── base.py             # BaseForecaster abstract class
│   ├── random_walk.py
│   ├── sarimax.py
│   ├── xgboost_m.py
│   └── ensemble.py
├── conformal.py            # split-conformal wrapper
├── backtest.py             # walk-forward + regime segmentation
└── interpret.py            # horizon-aware текстовая интерпретация

scripts/
├── forecast.py             # interactive CLI: python scripts/forecast.py brent 3m
└── eval/
    └── eval_forecast.py    # генерирует metrics/runs/<date>_forecast_<sha>.json

datasets/
├── forecast_history.csv    # snapshot 5 лет (commit, ~5-10 МБ) — для воспроизводимости бектеста
└── forecast_cache/         # gitignored — runtime кеш fetchers

metrics/runs/
└── 2026-05-06_forecast_<sha>.json
```

## Аргументация — главные неочевидные решения

### Почему RW обязателен в наборе

Без RW в бектест-таблице любая модель будет «лучше прошлого». Random walk — естественный baseline для финансовых рядов с unit root (а нефть и газ как раз такие). Если SARIMAX не бьёт RW на 1-3 мес — это **не повод выкинуть SARIMAX**, это сигнал что добавленная ценность мала и нужно так и сообщать пользователю. Без этой честности модуль превращается в plot generator с фальшивым ощущением точности.

### Почему именно SARIMAX + XGBoost (и больше ничего из stat/ML)

**SARIMAX (с экзогенами)** — единственная stat-модель, которая консистентно даёт прирост на нефти на 1-6 мес при правильно подобранных экзогенах (запасы EIA — топ-1 предиктор по Baumeister-Kilian). Аналитический CI «из коробки» — критично для интерпретируемости стейкхолдерам Сбера.

**XGBoost** — единственная ML-модель, которая на финансовых рядах не дискредитирует себя в production: быстрая, интерпретируемая через SHAP, не overfit'ит на коротких окнах как LSTM. На oil-рядах в comparison studies 2024-2025 регулярно бьёт LSTM и Transformer.

Третья stat-модель (Theta, ETS) или второй ML (LightGBM) — diminishing returns. Лучше потратить силы на качество бектеста + честный CI, чем расширять зоопарк.

### Почему conformal, а не bootstrap residuals для XGBoost

Bootstrap residuals предполагает iid остатки. На нефти после 2022 — заведомо не iid (структурный слом, регимы волатильности). Conformal prediction — distribution-free, держит nominal coverage даже при non-exchangeable данных (split-conformal), и при правильном выборе calibration window — стабильно. Trade-off: 20% данных уходит в calibration, train-set меньше. Для daily 5-летних рядов (~1250 точек) приемлемо.

### Почему walk-forward с regime segmentation

Аггрегатная метрика на 5 годах с break-points — **усреднённая ложь**. Модель может быть полезной в `cap_normalization` (2023-2025) и катастрофичной в `russia_war_shock` (2022). Эту картину должен видеть и аналитик в Сбере, и я при выборе default. Сегментированные метрики решают эту проблему.

### Почему `urals_minfin_blend` как отдельный output

Минфин РФ с 2025 считает «налоговую цену нефти» по официальной формуле `0.78 × Urals_FOB(Primorsk+Novorossiysk) + 0.22 × ESPO_FOB_Kozmino`. Прогноз именно этой агрегированной цены = прогноз нефтегаздоходной части бюджета. Для аналитика Сбера, оценивающего реалистичность бюджетного плана 2026-2028 (Минэк СЭР закладывает $59 Urals 2026), это критическая фича.

## Последствия

**Плюсы:**
- Полное покрытие нефть/газ/российский периметр (10 активов).
- Обоснованный выбор моделей с research-фундаментом (не just «потому что популярно»).
- Honest baseline (RW) делает все остальные метрики интерпретируемыми.
- Walk-forward с regime segmentation — production-grade бектест, не hand-waving.
- Conformal CI на ML — academically sound, не bootstrap-плацебо.
- Минфин-blend как unique demo-фича для Сбер-кейса.

**Минусы / риски:**
- 10 активов × 4 модели × 4 горизонта × 4 режима = большая поверхность бектеста. Eval-скрипт должен быть кешируемым и инкрементальным.
- Investing.com / SPIMEX скрейперы — fragile. Артём согласился на этот риск (демо разовое, после теста проект не востребован).
- ЦБ РФ-monthly даёт 60 точек на 5 годах — для XGBoost+conformal недостаточно. Решение: для этого asset — только RW + SARIMAX + ensemble(2), явно фиксируется.
- Spread-модель для Urals в 2022 будет работать плохо (regime break). Markov-Switching ловит, но он в PR3 — здесь честно фиксируем как known limitation.
- Conformal calibration на 5 годах ≈ 250 точек calib — статистически приемлемо, но не идеально (нужно бы 500+).

**Митигации:**
- Eval-скрипт пишет per-(asset×model×horizon×regime) кеш в JSON; пересчитывает только что изменилось.
- Снапшот 5-летней истории в `datasets/forecast_history.csv` коммитится — бектест воспроизводимым останется даже если все скрейперы сломаются.
- При недоступности live-fetch — graceful fallback на снапшот с явным `data_freshness` в metadata результата.

## Plan B — если PR1 не на ревью к 2026-05-09

Дедлайн ТЗ — 2026-05-12 12:00. Если в субботу 2026-05-09 PR1 ещё не отправлен на ревью, **режется scope** (по убыванию ценности):

1. **XGBoost + conformal CI → отрезаются.** Остаются `random_walk`, `SARIMAX`, `ensemble = mean(RW, SARIMAX)`. Покрытие ТЗ §2.5 «минимум 2 метода» сохранено.
2. **Regime-segmented бектест → aggregate-only.** Walk-forward остаётся, разбивка по 4 режимам убирается; в эксперименте — одна сводная таблица.
3. **Экзогены SARIMAX → отрезаются.** Univariate SARIMAX (только лаги цены и собственная сезонность). EIA-inventories/DXY как фичи отложены в PR3.
4. **Derived-активы (urals, espo, urals_minfin_blend) → один метод (через Brent_RW + spread).** Без выбора per-method, default — RW поверх.

После этого PR1 — это **3 модели на 7 observable assets + derived layer на 3 derived assets, walk-forward без режимов, без экзогенов**. Это всё ещё закрывает ТЗ полностью и оставляет 3 дня на PR2 (Ouroboros skill) и интеграционное тестирование демо.

## Что НЕ в этом PR

- **Ouroboros tool/skill обёртка** — отдельный PR `feature/forecast-skill` (PR2). Регистрирует `oil_gas_forecast(asset, horizon)` как Ouroboros-tool, интегрирует в LangGraph subgraph (узел `forecast_call`).
- **Pooled VAR Baumeister-Kilian** — нужны real-time vintages of macro data, отдельная инфраструктура. PR3.
- **GARCH-LSTM для шоковой волатильности** — особенно полезен для TTF/JKM. PR3.
- **Markov-Switching на Urals-Brent spread** — там, где режимная модель реально работает. PR3.
- **OPEC reference basket** — P1, добавлю если время в PR1, иначе отдельный мини-PR.
- **NBP, Новатэк-СПГ дисконт** — P2, после v1.0 deploy.
- **Брендинг ответов через LLM** — `interpret.py` детерминированно-шаблонный; LLM-обогащение интерпретации — отдельный PR `feature/forecast-llm-narrative`.

## Альтернативы рассмотренные

- **Prophet (Meta)** — отвергнут после research: на oil-рядах слабее RW в comparison studies 2023-2025. Зависимость тяжёлая (Stan), без выгоды.
- **ETS / Holt-Winters** — отвергнут: не показывает преимуществ перед SARIMAX на нефти.
- **LSTM / GRU / Transformer** — proxy-победы в литературе с data leakage, нестабильны в production. XGBoost лучше для аналогичной ниши.
- **CEEMDAN/VMD + ML гибриды** — высокий риск look-ahead bias при naive реализации; не оправдан в MVP.
- **pmdarima `auto_arima`** — иногда сломан после обновлений; ручной grid по AIC + ADF — 20 строк кода и стабильно.
- **Paid APIs** (OilPriceAPI, Commodities-API) — отвергнуты: $50-100/мес, для тестового задания нет смысла, free-источники достаточны.
- **Trading Economics CSV** для Urals — требует регистрации/подписки для daily history; investing.com даёт free.
- **Direct futures curves** через Eikon/Bloomberg — нет доступа.

## Ссылки

### Источники данных
- [EIA Open Data — register API key](https://www.eia.gov/opendata/register.php)
- [EIA STEO Crude Oil Price Methodology](https://www.eia.gov/analysis/handbook/pdf/STEO_Crude_Oil_Price.pdf)
- [Investing.com — Urals Spot historical](https://www.investing.com/commodities/crude-oil-urals-spot-futures-historical-data)
- [Investing.com — JKM LNG Platts](https://www.investing.com/commodities/lng-japan-korea-marker-platts-futures-historical-data)
- [СПбМТСБ — рынок газа](https://spimex.com/markets/gas/section/)
- [ЦБ РФ — экспорт газа (xls)](https://www.cbr.ru/vfs/statistics/credit_statistics/trade/gas.xls)
- [OPEC Basket Price (XML feed)](https://www.opec.org/opec_web/en/data_graphs/40.htm)

### Методические источники (research)
- [Baumeister-Kilian: Pooling Real-Time Oil Price Forecasts](https://www.eia.gov/workingpapers/pdf/oilprice_forecasts.pdf)
- [Bank of Canada: The Art and Science of Forecasting Real Price of Oil](https://www.bankofcanada.ca/wp-content/uploads/2014/05/boc-review-spring14-baumeister.pdf)
- [Federal Reserve IFDP: Forecasting the Price of Oil](https://www.federalreserve.gov/pubs/ifdp/2011/1022/ifdp1022.pdf)
- [Empirical Economics 2024: Beating end-of-month RW for oil](https://link.springer.com/article/10.1007/s00181-024-02599-8)
- [J Banking Finance 2023: Forecasts of real oil price revisited](https://www.sciencedirect.com/science/article/pii/S0378426623001619)
- [MDPI Energies 2025: XGBoost-LSTM Hybrid for Oil](https://www.mdpi.com/1996-1073/18/9/2246)
- [Conformal Prediction for Time Series — Xu & Xie](https://arxiv.org/pdf/2010.09107)
- [Adaptive Conformal Predictions for Time Series — Zaffran 2022](https://proceedings.mlr.press/v162/zaffran22a/zaffran22a.pdf)
- [Incorrys: Brent/Urals Differential 2022-2026](https://incorrys.com/energy/energy-price-forecast/brent-urals-differential/)
- [Bank of Finland Bulletin 2025: Falling oil prices and Russian budget](https://www.bofbulletin.fi/en/blogs/2025/falling-oil-prices-reduce-russia-s-budget-revenues/)

### Связанные проектные документы
- [docs/tz/original.md](../tz/original.md) — ТЗ §2.5
- [docs/architecture.md](../architecture.md) — место `brent_forecast` tool в LangGraph subgraph
- [docs/adr/0007-llm-providers.md](0007-llm-providers.md) — для будущей LLM-интерпретации
- [docs/adr/0009-corpus-strategy.md](0009-corpus-strategy.md) — fallback на сценарии при horizon ≥ 18m
