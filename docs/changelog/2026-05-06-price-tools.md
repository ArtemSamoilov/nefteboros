# Changelog: feature/price-tools — расчётный модуль прогноза цен нефти и газа

- **Дата:** 2026-05-06
- **PR:** `feature/price-tools` (PR1 из 2-3 в forecast-цепочке)
- **ADRs:** [0012 — модели и backtest](../adr/0012-price-tools.md), [0013 — hybrid forecasting (для PR2)](../adr/0013-hybrid-forecasting.md)

## Задача

Реализовать расчётный модуль прогноза цен (ТЗ §2.5):
- **Минимум 2 метода** прогноза с CI и краткой интерпретацией.
- **Brent** обязательно; в нашей версии — расширили до **10 P0 активов** (нефть глобальная + российская, газ глобальный + российский нефтегаз-proxy).
- **Агент сам решает** когда вызвать (это в PR2 — `feature/forecast-skill`).

## Контекст

Дискуссии с Артёмом (см. сессию):
- **Расширение scope** под фокус «аналитик Сбера»: Urals и ESPO критичны (госбюджет РФ зависит от Минэк-формулы 2025+: 0.78×Urals + 0.22×ESPO). Газ — равноправный с нефтью (Газпром, Новатэк).
- **Strict separation** после self-bombing review: модели обучаются только на наблюдаемых рядах; российские нефтесорта (urals, espo) и blend — derived layer поверх Brent forecast (исключает circular reasoning, при котором модель «учится на собственной формуле reconstruction»).
- **MOEX ISS API discovery:** во время research нашлось, что российские акции/индексы доступны через публичный REST API Московской биржи без VPN. Это game-changer — заменили SPIMEX/CBR (закрытые/мёртвые) на MOEXOG / GAZP / NVTK как **финансовый proxy** российского нефтегаз-сектора.
- **JKM (Asian LNG) → P2:** investing.com отдаёт только последний месяц через `__NEXT_DATA__`, AJAX-endpoint требует реверса. В interpret.py для Asian gas TTF используется как proxy.

## Что сделано

### Архитектура (ADR-0012)

Документ ~400 строк со всеми архитектурными решениями + Discovery-секцией о том, что не работает в открытых источниках, чтобы будущие авторы не наступали на те же грабли.

Ключевые решения:
- **4 модели:** Random Walk (honest baseline), SARIMAX (с экзогенами), GBR (sklearn в PR1, XGBoost в PR3), Ensemble = mean(SARIMAX, GBR).
- **4 горизонта:** 1m / 3m / 6m / 12m. На `>=18m` — `ForecastRefusal` + redirect на сценарии в RAG (WOO, IEA Oil, ИНЭИ).
- **Walk-forward бектест** с rolling origin + regime segmentation по 4 режимам (pre_war / russia_war_shock / cap_normalization / iran_2026).
- **MASE vs RW** — главный критерий качества (бьёт ли модель persistence).
- **Strict separation** для derived активов (urals, espo, urals_minfin_blend).
- **Plan B:** если PR1 не на ревью к 2026-05-09 — режется XGBoost + conformal + regime-segmentation.

### Контракты и реестр

- `nefteboros/forecast/schema.py` — pydantic-контракты: `Horizon`, `AssetID` (Literal), `ModelMethod`, `ConfidenceInterval`, `ForecastPoint`, `ForecastResult`, `ForecastRefusal`, `BacktestMetrics`, `BacktestSummary`, `AssetGroup`, `DataSource`, `Frequency`, `BacktestRegime`.
- `nefteboros/forecast/registry.py` — реестр 11 активов с метаданными. Runtime-assertion: `_check_registry_matches_schema()` ловит расхождение AssetID Literal и registry keys на импорт.

### Data layer

10 P0 активов покрыты:

| Источник | Активы | Файл |
|---|---|---|
| yfinance | brent, wti, henry_hub, ttf + DXY | `data/yf.py` |
| EIA REST API v2 | brent_spot, wti_spot, hh_spot + 3 inventories | `data/eia.py` |
| MOEX ISS REST | moexog, gazp, nvtk (paginated) | `data/moex.py` |
| derived | urals, espo, urals_minfin_blend (Minfin piecewise) | `data/derived.py`, `data/spread_schedule.py`, `derived_layer.py` |

- `cache.py` — TTL CSV-кеш с graceful fallback на stale cache при сбое live-fetch.
- `spread_schedule.py` — hardcoded Brent-Urals/ESPO discount по 4 режимам, источники (Bruegel WP 32/2025 + Минэк). Документировано в ADR.

### Модели

- `models/base.py` — `BaseForecaster` ABC с единым контрактом fit/predict.
- `models/random_walk.py` — empirical-residual CI (без gaussian-предположений).
- `models/sarimax.py` — `statsmodels.SARIMAX` с auto-grid по AIC из 5 кандидатных порядков; аналитический CI «из коробки».
- `models/xgboost_m.py` — sklearn `GradientBoostingRegressor` с recursive multi-step + split-conformal CI. Имя метода `XGBoost` для consistency публичного API; XGBoost-replacement в PR3.
- `models/ensemble.py` — простое усреднение (mean point + union CI).
- `conformal.py` — split-conformal с rolling calibration window (literature: Xu & Xie 2020).

### Forecast layer

- `derived_layer.py` — Brent forecast → Urals/ESPO/blend через spread с CI расширением (convolution).
- `api.py` — высокоуровневый `forecast(asset, horizon, method=None) -> ForecastResult | ForecastRefusal`. Парсинг horizon (1m/3m/6m/12m), отказ на >= 18m, log-transform для газовых, default-метод per horizon.
- `interpret.py` — детерминированный horizon-aware текст; явные disclaimers для derived, univariate-proxy, gas (high-volatility periods).
- `backtest.py` — walk-forward с rolling origin, regime segmentation, 6 метрик.

### CLI и eval

- `scripts/forecast.py` — interactive CLI (`python scripts/forecast.py brent 3m`).
- `scripts/eval/eval_forecast.py` — full backtest grid (7 assets × 4 methods × 4 horizons = 112 configurations) с incremental cache в `metrics/runs/<date>_forecast_<sha>.json`.

### Документация

- `docs/adr/0012-price-tools.md` — основное ADR (модели, источники, бектест, strict-separation).
- `docs/adr/0013-hybrid-forecasting.md` — **новое ADR для PR2**: гибридный пайплайн (stat-модели + RAG + web-search) после live-теста, показавшего ограничения чисто time-series подхода.
- `docs/experiments/forecast.md` — таблицы метрик, **live-test 2026-02→05 (Iran-shock −38% по Brent)**, обоснование выбора моделей, heavy-tail warning, известные ограничения.
- `docs/changelog/2026-05-06-price-tools.md` — этот файл.

### Зависимости и env

- `requirements-domain.txt` обновлён: добавлен `python-dotenv`. Закомментирован `prophet` (отвергнут в ADR — на нефти слабее RW). Закомментирован `xgboost` как PR3-future (требует libomp).
- `.env.example` обновлён: новые `EIA_API_KEY`, `FORECAST_CACHE_DIR`, `FORECAST_CACHE_TTL_HOURS`, `FORECAST_HISTORY_YEARS`, `FORECAST_BACKTEST_*`.

## Что НЕ в этом PR

- **Ouroboros tool/skill обёртка** — отдельный PR2 `feature/forecast-skill`. Регистрирует `oil_gas_forecast(asset, horizon)` как Ouroboros-tool, интегрирует в LangGraph subgraph (узел `forecast_call`).
- **JKM Asian LNG fetcher** — отложен в P2. В interpret для Asian gas TTF используется как proxy.
- **OPEC reference basket fetcher** — P1, отдельный мини-PR при наличии времени.
- **NBP UK gas, Новатэк-СПГ дисконт** — P3.
- **Pooled VAR Baumeister-Kilian** — нужны real-time vintages of macro data, отдельная инфраструктура. PR3.
- **GARCH-LSTM** для шоковой волатильности (особенно TTF) — PR3.
- **Markov-Switching** на Urals-Brent spread — PR3.
- **RU-macro экзогены** для MOEXOG/GAZP/NVTK (USD/RUB через MOEX ISS, ставка ЦБ) — PR3.

## Тесты

- AST-парсинг прошёл по всем 14 .py файлам в `nefteboros/forecast/` + 2 скриптам.
- Smoke-тесты на real data:
  - yfinance — 4 актива + DXY (5y daily, n=1257-1258).
  - EIA — 3 spot + 3 inventory (5y daily/weekly, n=260-1254).
  - MOEX ISS — 3 ticker (5y daily paginated, n=1251-1345).
  - Synthetic Urals/ESPO/blend — sanity check periodov + Минфин-формула (diff = 0.0000).
  - 4 модели на Brent — все fit + predict работают.
  - api.forecast() — 5 кейсов: Brent SARIMAX 3m, Urals derived 3m, blend 6m, MOEXOG 1m, refusal 24m.
  - CLI — Brent 3m + 24m refusal.
  - Backtest RW vs SARIMAX на Brent 3m: RW MAPE 8.96%, SARIMAX MAPE 9.15% MASE 1.02 (немного хуже на point-accuracy, +38% directional accuracy).

Полноценные unit-тесты с моками — отложены в PR3 (для тестового задания not blocker).

## Файлы

**Добавлено (~24 файла):**
- `nefteboros/forecast/api.py`
- `nefteboros/forecast/backtest.py`
- `nefteboros/forecast/cache.py`
- `nefteboros/forecast/conformal.py`
- `nefteboros/forecast/derived_layer.py`
- `nefteboros/forecast/interpret.py`
- `nefteboros/forecast/registry.py`
- `nefteboros/forecast/schema.py`
- `nefteboros/forecast/data/__init__.py`, `yf.py`, `eia.py`, `moex.py`, `spread_schedule.py`, `derived.py`
- `nefteboros/forecast/models/__init__.py`, `base.py`, `random_walk.py`, `sarimax.py`, `xgboost_m.py`, `ensemble.py`
- `scripts/forecast.py`
- `scripts/eval/eval_forecast.py`
- `datasets/forecast_manual/.gitkeep`, `README.md` (как опция для VPN-fetched данных)
- `docs/adr/0012-price-tools.md`
- `docs/experiments/forecast.md`
- `docs/changelog/2026-05-06-price-tools.md`
- `metrics/runs/2026-05-06_forecast_<sha>.json` (бектест-результаты)

**Изменено:**
- `nefteboros/__init__.py` — обновлён план forecast.
- `nefteboros/forecast/__init__.py` — переписан с lazy-импортами.
- `requirements-domain.txt` — обновлены deps.
- `.env.example` — обновлены forecast vars.

**Удалено:** —

## Связанные документы

- ADR-0012: [docs/adr/0012-price-tools.md](../adr/0012-price-tools.md)
- Эксперимент: [docs/experiments/forecast.md](../experiments/forecast.md)
- Предыдущие PR: feature/server-fixes (#4), feature/corpus-bootstrap (#5), feature/rag-extract (#6)

## Главный наблюдаемый результат — live-test Iran-shock

Самый важный финдинг этого PR — **наглядное свидетельство ограничений чисто time-series подхода**:

```
Cutoff: 2026-02-06  →  Target: 2026-05-06  (горизонт 3m)

Brent  RW       $68 →  предсказано $68,  факт $110  ошибка −38.1%, MISS CI80
WTI    SARIMAX  $64 →  предсказано $64,  факт $102  ошибка −37.6%, MISS CI80
Urals  RW       $51 →  предсказано $51,  факт $93   ошибка −45.0%, MISS CI80
TTF    XGBoost  €36 →  предсказано €35,  факт €47   ошибка −25.9%, MISS CI80
HH     XGBoost  $3.4 → предсказано $4.0, факт $2.79  ошибка +44.8%, ✓ CI80
MOEXOG Ensemble 6895→ предсказано 7009,  факт 6780   ошибка +3.4%,  ✓ CI80
GAZP   SARIMAX  ₽126→ предсказано ₽126,  факт ₽119   ошибка +5.6%,  ✓ CI80
NVTK   XGBoost  ₽1162→ предсказано ₽1170, факт ₽1145 ошибка +2.1%,  ✓ CI80
```

В Feb-2026 рынок был в «cap_phase_2 stable» ($50-75); ни одна стат-модель не предсказала бы Iran-эскалацию по price-history. Goldman Sachs / JPMorgan / OPEC — никто не предсказал. Информация о грядущем conflict'е жила в **тексте RAG-источников** (CRS Iran 26.03.2026, Bruegel WP), не в исторических ценах.

**Это не баг моделей, это фундаментальное ограничение time-series методов на политически-чувствительных рядах.** Решение зашито в новой ADR-0013: гибридный пайплайн, где forecast = base-case + scenarios from RAG + recent_events from web-search.

`interpret.py` обновлён — каждый прогнозный ответ содержит явный disclaimer про необходимость дополнения RAG/web для production-аналитики.

## Известные ограничения (cм. ADR-0012)

- Российский газ daily — нет в открытых источниках (СПбМТСБ за платным каналом, CBR-feed мёртв с 2022). Покрытие — через MOEXOG/GAZP/NVTK как **финансовый proxy сектора**, не цена газа per se. В `interpret.py` для российских газовых вопросов агент явно говорит об этом ограничении.
- Urals/ESPO daily 5y — нет (investing.com обрывается в Feb 2025, ESPO 404). Покрытие — через **derived layer** = Brent + hardcoded quarterly spread из Bruegel WP 32/2025 + Минэк. CI расширен на spread-uncertainty.
- TTF max 2022 = $339 (war shock) — heavy-tail residuals; SARIMAX coverage может проседать на этом периоде. log-transform применяется, но shock полностью не нейтрализуется.
- XGBoost в PR1 заменён на sklearn.GradientBoostingRegressor (отсутствие libomp на macOS dev-машине). Метрики ожидаемо на 5-10% хуже vs реальный XGBoost — будет переключено в PR3.
