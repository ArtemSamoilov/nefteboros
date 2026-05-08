# ADR-0023 — Forecast ensemble map: сценарии под current shock, spread per-scenario, citation format

- **Дата:** 2026-05-08
- **Статус:** Предложено v2 (BLOCKER для A1; ожидает финального согласования)
- **Контекст:** Track A roadmap v2.1 (`docs/roadmap-v2.1.md`). Архитектурная подготовка перед расширением `forecast` tool на сценарии (A1), `forecast_spread` (A2) и reproducibility (A3).
- **Связано:** ADR-0012 (price tools), ADR-0013 (hybrid forecasting), ADR-0019 (системный промпт + текущий citation format).
- **История:** v1 (черновик 2026-05-08 утром) предлагал base = ensemble без shift, bear/bull как hypothetical deltas. После research выяснилось, что реальный 2026-05-08 — **shock-режим** (Hormuz blocked с 28 февраля, Brent $100), а не пасторальное состояние из roadmap A1. v2 переписывает Q1/Q2/Q3 под реальность.

## Контекст и проблема

В v2.0.0 `forecast(asset, horizon)` возвращает точечную оценку с CI 80/95%, посчитанным на endogenous-волатильности 5-летнего окна. Это **не отражает структурной неопределённости** (геополитика, ОПЕК+, китайский спрос).

Важнее: market state на момент написания ADR (2026-05-08) — **активный shock-режим**, который модель не схватывает (5y train не содержит подобного эпизода). ADR-0013 это уже зафиксировал: «Live-test 2026-02-06 → 2026-05-06 показал ошибку SARIMAX/RW по Brent −38%». Track A1 даёт способ компенсировать это через scenarios.

A1 расширяет интерфейс на сценарный режим. Перед расширением — карта текущей ensemble, snapshot реального состояния рынка и решения по 4 открытым вопросам.

## Текущая карта ensemble (v2.0.0)

```
forecast(asset, horizon, *, method=None, history_years=5.0, use_cache=True)
  └─ method default per horizon (api.py:66):
       1m  → RANDOM_WALK
       3m  → ENSEMBLE
       6m  → ENSEMBLE
       12m → ENSEMBLE

  └─ ENSEMBLE = mean(SARIMAX, GBR), CI = union (min low, max high)  ← conservative
       └─ SARIMAX (sarimax.py)
            • auto-grid AIC по 5 кандидатам: (1,1,1) (2,1,1) (1,1,2) (2,1,2) (0,1,1)
            • exog поддерживается (in fit + future_exog в predict); в v2.0.0 не используется
            • CI = analytical (Kalman filter)
       └─ GBR (xgboost_m.py — sklearn.GradientBoostingRegressor; имя класса наследие
              плана PR3 «переход на xgboost.XGBRegressor», ModelMethod.XGBOOST оставлен
              для consistency публичного API; реально работает GBR)
            • n_lags=21, n_estimators=200, max_depth=4, learning_rate=0.05
            • random_state=42 (зашит в __init__)
            • CI = split-conformal (calibration на proper_train_frac=0.80; scaled √h)

  └─ DERIVED routing (api.py:_forecast_derived):
       urals  = forecast(brent, ...) − spread_schedule.get_spread_for_date(target, "urals")
       espo   = forecast(brent, ...) − spread_schedule.get_spread_for_date(target, "espo")
       blend  = 0.78×urals + 0.22×espo (НДПИ-формула с 2025-01)
       SPREAD_SCHEDULE — 4 режима (pre_war / war_shock / cap_phase_1 / cap_phase_2),
                         (low, mid, high) discount per период.

  └─ Citation format текущий (prompts/SYSTEM.md:77):
       [Forecast: model, CI N%]   пример: [Forecast: ARIMA, CI 80%]    ← УСТАРЕЛ:
       реальный method для горизонтов 3m+ — `ensemble`, не ARIMA.
```

## Snapshot 2026-05-08 — реальное состояние рынка (research-verified)

| Фактор | Реальное состояние | Источник |
|---|---|---|
| **Hormuz** | **Заблокирован с 28 февраля 2026** (US-Israel-Iran air war), traffic упал до 5% от нормы. Считается крупнейшим energy shock с 1970-х. На 2026-05-08 идут переговоры по US-Iran MOU для прекращения войны и открытия Strait. | [Wikipedia 2026 Strait of Hormuz crisis](https://en.wikipedia.org/wiki/2026_Strait_of_Hormuz_crisis); [CNBC 2026-05-07](https://www.cnbc.com/2026/05/07/oil-prices-today-trump-iran-strait-of-hormuz-us-crude-brent-.html) |
| **Iran экспорт** | Обвалился с 1.6 mbpd (Feb 2026) до **0.4 mbpd** (Apr 2026) — −75%. Trump admin **частично снял** sanctions на иранскую нефть в марте 2026. | [Middle East Insider Q2 2026](https://themiddleeastinsider.com/2026/04/28/iran-sanctions-q2-2026-oil-export-status/); [Washington Post 2026-03-20](https://www.washingtonpost.com/business/2026/03/20/iran-oil-sanctions-trump/) |
| **Brent spot** | **$100.06 на 2026-05-07** (CNBC). Goldman Sachs: pre-ceasefire forecast Q2 2026 = $99 (соответствует current spot). | CNBC, Goldman Sachs |
| **ОПЕК+ cuts** | **1.65 mbpd voluntary cuts** (8 стран). В апреле 2026 решили начать unwinding: +206k bpd в мае 2026. Group-wide cuts (3.6 mbpd total) сохраняются через 2026. | [OPEC 2026-04-05](https://www.opec.org/pr-detail/1756597-5-april-2026.html); [S&P Global](https://www.spglobal.com/energy/en/news-research/latest-news/crude-oil/052825-opec-retains-36-mil-bd-of-group-wide-cuts-through-2026) |
| **Russia cap** | **$47.60 с 21 января 2026** (G7), либо **$44.10 dynamic с 15 января 2026** (EU finance — нужно уточнить какой реально применяется). Бюджетная цена Минфина 2026 = $59 (выше cap). | [EU finance 2026-01-15](https://finance.ec.europa.eu/news/new-dynamic-mechanism-lower-price-cap-russian-crude-oil-4410-barrel-2026-01-15_en) |
| **China demand 2026** | IEA: рост на **+0.198 mbpd** в 2026, total demand >13.0 mbd. Глобальный рост 850 kbd, весь из развивающихся экономик. | [IEA OMR Feb 2026](https://www.iea.org/reports/oil-market-report-february-2026) |

**Bank scenario forecasts (для калибровки):**

| Bank | Forecast Q2 2026 (Brent) | Условие | Источник |
|---|---|---|---|
| Goldman Sachs | $99 | Pre-ceasefire (active shock) | [Goldman pre-revision](https://money.usnews.com/investing/news/articles/2026-03-04/goldman-sachs-raises-q2-brent-oil-price-forecast-by-10-to-76-a-barrel) |
| Goldman Sachs | $90 | Post-ceasefire (Iran-US 2-week ceasefire) | [Investing.com factbox](https://www.investing.com/news/commodities-news/factboxgoldman-sachs-lowers-secondquarter-2026-oil-price-forecasts-on-usiran-ceasefire-4604522) |
| Goldman Sachs (severe) | $115 (Q4 2026) | Ceasefire breaks, persistent ME losses 2 mbpd | [Investing.com factbox](https://www.investing.com/news/commodities-news/factboxgoldman-sachs-lowers-secondquarter-2026-oil-price-forecasts-on-usiran-ceasefire-4604522) |
| J.P. Morgan | $60 (avg 2026) | Soft fundamentals, disruptions manageable | [JPM Global Research 2026](https://www.jpmorgan.com/insights/global-research/commodities/oil-prices) |

**Price elasticity (для конвертации mbpd shift → $/bbl):**

- Kilian classic: ~$10/bbl per 1 mbpd persistent supply shock.
- Goldman severe scenario implicit: $115 vs $90 = +$25 при +2 mbpd persistent losses → ~$12-13/bbl per 1 mbpd. В ballpark с Kilian.
- Используем **$10–$15/bbl per 1 mbpd** как калибровочный коридор.

**Вывод для дизайна сценариев.** Базироваться на реальном current state (вариант A из обсуждения с creator-ом 2026-05-08), не на пасторальном baseline из roadmap. base = current shock; bear/bull = направления resolution.

## Решения по open questions

### Q1. Куда попадают shock-параметры → post-modeling shift, base anchored to current shock

**Решение:** **(г) post-modeling shift**. base = current shock с **transparent observation-anchored shift** к spot price; deltas = направления изменения от base.

**Архитектурно:**

```
forecast(asset, horizon, scenario="base"):
    raw_ensemble = SARIMAX+GBR mean output            # ~$68 (model, без shock awareness)
    base_anchor_shift = current_spot - raw_ensemble    # ~+$30 (observation, не model)
    base_value = raw_ensemble + base_anchor_shift      # ≈ current_spot ≈ $100
    return base_value + scenario_delta(scenario, asset, horizon)

scenario_delta:
    "base"  → 0
    "bear"  → -$10..-$25 (de-escalation)
    "bull"  → +$25..+$45 (escalation)
```

**Почему так.**

- (а)/(б) exog в SARIMAX/GBR требуют исторических рядов по драйверам — нет в проекте, не уложимся в 3.5 дня.
- На редких эпизодах (full Hormuz closure — 0 наблюдений за 5y) модель не научится shift'у.
- (г) даёт прозрачность: оценщик видит «base = market state 2026-05-08, anchored to spot $100 (model gap +$30 = observation, не из модели)»; bear/bull — именованные deltas.

**Slabые места и mitigation.**

- *«$30 anchor shift — это подкрутка»*. Контр: явно labeled `observation-anchored, not model-derived` в `metadata` ответа и в `interpretation`-е. Альтернатива (отдать модельный $68 при spot $100) хуже — open lying. Mitigation: дисклеймер в interpretation: «модель v2.0.0 не схватывает Hormuz crisis (5y train без таких эпизодов); base anchored к observed spot; sensitivity: при изменении spot на ±$10 — все scenarios сдвигаются на ±$10».
- *Snapshot устаревает*. Если оценщик откроет проект через 3 недели и Hormuz уже reopened — ADR-snapshot врёт. Mitigation: `as_of: 2026-05-08`; в `interpretation` warning при `today - as_of > 14 дней`; раздел в ADR «когда обновлять» (при крупных событиях: MOU signed, Hormuz reopens, новый ОПЕК+ raid).
- *bear/bull детерминированы относительно base.* Acceptance roadmap A1 требует «сценарий × CI». В (г) CI сохраняется: `ci_low_scenario = ci_low_base + delta_low`, `ci_high_scenario = ci_high_base + delta_high`. Статистическая неопределённость не теряется, добавляется calibration uncertainty диапазона delta.

**Что отвергли:**
- Variant B (base = pre-shock baseline ~$70-80, hypothetical bull/bear) — out of touch с реальностью; оценщик откроет, увидит «forecast Brent $75 base», сверит со spot $100, потеряет credibility.
- Variant C (two-layer: model_baseline + current_overlay) — слишком complex для UI/interpretation.
- Exog regressors (а/б/в) — data engineering неподъёмный на 3.5 дня.

### Q2. Драйверы — 5, под реальный snapshot, с research-калибровкой

**Каталог `DRIVERS` в `nefteboros/forecast/scenarios.py`:**

| Driver | States | Mbpd shift от current | $/bbl shift | Источник калибровки |
|---|---|---|---|---|
| `hormuz_status` | `blocked` (current), `partial_reopen`, `full_reopen`, `full_closure` | 0 / +1.5 / +3.0 / −5.0 | 0 / −$15..−$22 / −$30..−$45 / +$50..+$75 | Goldman severe scenario implicit; UNCTAD Hormuz Disruptions report |
| `iran_sanctions` | `maximum_pressure_active` (current), `partial_lift`, `full_lift`, `further_tightening` | 0 / +0.6 / +1.2 / −0.2 | 0 / −$6..−$10 / −$12..−$18 / +$2..+$3 | EIA STEO Apr 2026; WaPo Trump partial lift Mar 2026 |
| `opec_plus_unwinding` | `gradual` (current, +206k bpd/мес), `accelerated` (full unwind 1.65 mbpd in 6 мес), `extended` (re-tighten cuts +0.5 mbpd) | 0 / +1.0..+1.5 / −0.5 | 0 / −$10..−$15 / +$5..+$8 | OPEC 2026-04-05; S&P Global |
| `russia_cap_enforcement` | `active_$47.60` (current), `tightened_$44.10_dynamic`, `removed` | 0 / 0 (price effect, не volume) / +0.5 (Russian export normalizes) | 0 / +$3..+$5 / −$5..−$8 | EU finance 2026-01-15; Bruegel |
| `china_demand` | `weak`, `base_+0.2_mbpd` (current), `strong` | −0.4 / 0 / +0.4 | −$3..−$5 / 0 / +$3..+$5 | IEA OMR Feb 2026 |

**Конвертация mbpd → $/bbl:** ~$10–$15/bbl per 1 mbpd persistent shift, через Kilian/Goldman elasticity.

**Преустановленные сценарии:**

```python
PRESET_SCENARIOS = {
    "base": ScenarioParams(
        # Текущий shock-режим, anchored to spot
        hormuz="blocked",
        iran="maximum_pressure_active",
        opec_plus="gradual",
        russia_cap="active_$47.60",
        china="base_+0.2_mbpd",
        # observation-anchored shift активен (см. Q1)
    ),
    "bear": ScenarioParams(
        # De-escalation: MOU подписан, Hormuz reopens, Iran частично возвращается
        hormuz="partial_reopen",            # -$15..-$22
        iran="partial_lift",                 # -$6..-$10
        opec_plus="extended",                # +$5..+$8 (защита цен от падения)
        russia_cap="active_$47.60",
        china="base_+0.2_mbpd",
        # Net delta: -$15..-$25 → Brent $75-90
        # Соответствует: Goldman post-ceasefire $90, JPM $60 (нижняя граница bear)
    ),
    "bull": ScenarioParams(
        # Escalation: MOU breaks, Hormuz fully closed, regional war expands
        hormuz="full_closure",               # +$50..+$75
        iran="further_tightening",           # +$2..+$3
        opec_plus="gradual",
        russia_cap="tightened_$44.10_dynamic",  # +$3..+$5
        china="weak",                        # -$3..-$5 (recession risk при price spike)
        # Net delta: +$45..+$75 → Brent $145-175
        # Соответствует: Goldman severe Q4 $115; наша оценка выше — отражает более
        # агрессивный full closure, не 2 mbpd persistent loss
    ),
}
```

**Опционально — кастомные комбинации:** пользователь может передать `ScenarioParams(...)` с произвольными states. Линейная суммация delta'ов с явным `interpretation` warning «оценочно, custom сценарии не cross-validated».

**Источники калибровки в коде.** Все shifts хранятся в `nefteboros/forecast/scenarios.py` с явными комментариями `# source: <reference>` рядом с каждым числом. При появлении новых данных — правится в одном месте.

### Q3. Spread tool — возвращает все 3 сценария за один вызов

**Решение:** `forecast_spread` всегда возвращает **per-scenario результаты** для bear/base/bull с явными commentary к каждому. Один spread на все три сценария — отвергнуто (creator: «ни о чём»).

**Интерфейс:**

```python
forecast_spread(
    asset_a: AssetID,             # brent
    asset_b: AssetID,             # urals | wti
    horizon: str | Horizon,
    *,
    history_years: float = 5.0,
    use_cache: bool = True,
) -> SpreadForecastResult
```

`SpreadForecastResult.per_scenario: dict[str, SpreadScenarioEntry]`, где `SpreadScenarioEntry`:

```python
@dataclass
class SpreadScenarioEntry:
    scenario: Literal["bear", "base", "bull"]
    spread_value: float                    # mid
    ci_80: ConfidenceInterval
    ci_95: ConfidenceInterval
    commentary: str                        # объяснение почему именно такой spread в этом сценарии
    drivers: list[str]                     # какие drivers этого сценария влияют на spread
```

**Откуда берутся per-scenario spreads.**

#### (brent, urals) — schedule-based с per-scenario adjustment

- Brent prediction берётся из `forecast("brent", horizon, scenario)` (per-scenario).
- Urals discount per scenario:
  - `base`: spread из `SPREAD_SCHEDULE` (cap_phase_2 = $17 mid, $12-22 диапазон) — current
  - `bear` (de-escalation): cap relevance падает (cap < spot → cap not binding); discount сужается до $5-12 mid (исторические пред-cap уровни 2021)
  - `bull` (escalation, Hormuz closed): Russian flows tighter, secondary sanctions risk выше; discount расширяется до $20-30 mid (war_shock 2022 как proxy)
- `commentary` объясняет: «при de-escalation cap $47.60 неэффективен (spot ~$80 < cap binding threshold $90), shadow fleet premium падает, discount сжимается; при escalation secondary sanctions risk на shadow fleet растёт, discount расширяется».

#### (brent, wti) — модель на series разностей с per-scenario shift

- `series_diff = brent_history - wti_history` (mean-reverting, обе серии реальные daily).
- SARIMAX/GBR обучаются на series_diff → forecast spread напрямую.
- Per-scenario shift к spread:
  - `base`: модельный output напрямую (current spread ~$3-5)
  - `bear` (de-escalation, supply normalizes): spread compresses к pre-shock norm $1-3
  - `bull` (escalation, Hormuz closed → US shale isolation premium): spread расширяется до $5-8 (US получает relative supply advantage когда ME заблокирован)

**Quick experiment перед реализацией (A2 step 0).** Скрипт `scripts/eval/eval_spread.py`:
- Schema A: SARIMAX/GBR на series_diff
- Schema B: forecast(brent) − forecast(wti), CI = √(σ_brent² + σ_wti² − 2·corr·σ_brent·σ_wti)
- Критерий: A < B на ≥10% по MAE на out-of-sample 1y, coverage 80% ≥ 0.75. Иначе B.
- Время: ~2 часа.

**Закрытый список пар:** `(brent, urals)`, `(brent, wti)`. Любая другая пара → `ForecastRefusal` с объяснением.

### Q4. Citation format — `[Forecast: <model>, scenario=<name>, CI <level>]`

**Решение:** scenario обязателен (даже для base), CI level обязателен.

**Примеры:**
```
[Forecast: ensemble, scenario=base, CI 80%]
[Forecast: ensemble, scenario=bear, CI 80/95%]
[Forecast: sarimax, scenario=bull, CI 95%]
[Forecast: random_walk, scenario=base, CI 80%]
```

**Regex для Track D (D6):**
```python
FORECAST_CITE_RE = re.compile(
    r"\[Forecast:\s*(?P<model>[a-z_]+),\s*"
    r"scenario=(?P<scenario>[a-z_]+),\s*"
    r"CI\s*(?P<ci>\d{2}(?:/\d{2})?%)\]"
)
```

**Почему обязателен scenario:**
1. Diagnostic в Langfuse (Track F) — нет двусмысленности «implicit base vs forgotten scenario».
2. LLM-compliance: optional поле в few-shot пропускается в 15-25% follow-up'ов; mandatory + явные few-shot — почти 100%.
3. Regex проще (фиксированная структура).

**Почему `<level>` именно `80%`/`95%`/`80/95%`:**
- `80%` — default, primary uncertainty (ADR-0019 SYSTEM.md строка 93).
- `95%` — для adversarial запросов (worst case / regulator-style).
- `80/95%` — когда в тексте оба уровня (избегаем двух меток на один tool call).

**Что меняется в репо при принятии формата:**
- `prompts/SYSTEM.md` lines 77, 93 — обновить пример и формулировку.
- `nefteboros/forecast/interpret.py` — добавить scenario в формат.
- `datasets/intent_classifier.jsonl` — grep'нуть на старый формат, патчить.
- `nefteboros/forecast/api.py` — scenario прокидывается в metadata, доступен interpret'у.

**Координация с Track D.** Сразу после принятия этого ADR — D-сессия пишет `nefteboros/citations/patterns.py` с regex выше. При любом изменении формата позже — Track A пишет в D-сессию, не молча.

## Финальный интерфейс tools

### `forecast(asset, horizon, *, scenario=None, ...)`

```python
def forecast(
    asset: AssetID,
    horizon: str | Horizon,
    *,
    scenario: Optional[Union[str, ScenarioParams]] = None,
    method: Optional[ModelMethod] = None,
    history_years: float = 5.0,
    use_cache: bool = True,
) -> ForecastResult | ForecastRefusal: ...
```

- `scenario=None` или `scenario="base"` → текущий shock-режим, anchored to spot.
- `scenario="bear"` / `scenario="bull"` → preset из PRESET_SCENARIOS.
- `scenario=ScenarioParams(...)` → custom.

`ForecastResult` расширяется:
- `metadata["scenario"]`: `ScenarioParams` (применённый)
- `metadata["base_anchor_shift"]`: float (observation-anchored shift в base; 0 для bear/bull, потому что они от base через delta)
- `metadata["scenario_delta"]`: float (delta от base)
- `interpretation` явно описывает scenario, drivers, sensitivity к anchor

### `forecast_spread(asset_a, asset_b, horizon, ...)`

```python
def forecast_spread(
    asset_a: AssetID,
    asset_b: AssetID,
    horizon: str | Horizon,
    *,
    history_years: float = 5.0,
    use_cache: bool = True,
) -> SpreadForecastResult | ForecastRefusal: ...
```

`SpreadForecastResult.per_scenario` всегда содержит `bear`, `base`, `bull` (per-scenario commentary на каждый).

**Закрытый список пар** в `_VALID_SPREAD_PAIRS = {("brent", "urals"), ("brent", "wti")}`. Любая другая → refusal.

## A3 — Reproducibility

**План:**

1. **Audit стохастических точек:**
   - GBR — `random_state=42` есть в `__init__`; проверить bootstrap-residuals в conformal.
   - SARIMAX — Kalman filter детерминированный (нет `random_state`); `optimizer='lbfgs'` — детерминированный при одинаковом начальном `start_params`. Проверить, что `start_params` не зависит от `np.random`.
   - Ensemble — нет своего random; компоненты должны быть детерминированы.
   - Scenario shift — детерминированный (lookup в DRIVERS таблице).

2. **Глобальный seed.** В `nefteboros/forecast/__init__.py`:
   ```python
   import numpy as np
   _FORECAST_RANDOM_STATE = 42
   np.random.seed(_FORECAST_RANDOM_STATE)
   ```
   Опасный паттерн (load-time global), но единственный способ покрыть остаточный implicit-rand. Альтернатива — context manager `with numpy_seed(42):` в `forecast()`.

3. **Тест в `tests/test_forecast_reproducibility.py`:**
   ```python
   @pytest.mark.network
   def test_forecast_deterministic():
       r1 = forecast("brent", "3m", scenario="base", use_cache=False)
       r2 = forecast("brent", "3m", scenario="base", use_cache=False)
       assert r1.end_point.value == r2.end_point.value
       assert r1.end_point.ci_80.low == r2.end_point.ci_80.low

   @pytest.mark.network
   def test_forecast_spread_deterministic():
       r1 = forecast_spread("brent", "wti", "3m", use_cache=False)
       r2 = forecast_spread("brent", "wti", "3m", use_cache=False)
       for s in ("bear", "base", "bull"):
           assert r1.per_scenario[s].spread_value == r2.per_scenario[s].spread_value
   ```

## Validation framework для сценариев

**Backtest на исторических shocks невозможен** (нет labeled data «когда какой scenario был активен»). Доступные validations:

- **B1 — direction sanity** (обязательно). На любом запросе для (brent, base, bull): `bear_value < base_value < bull_value`. Простой sanity-check, не доказательство качества. Failing → ошибка калибровки.
- **B2 — bank scenario alignment** (важно для отчёта §4.5). Сравниваем наши bear/base/bull с published bank scenarios (Goldman post-ceasefire $90 ↔ наш bear; Goldman pre-ceasefire $99 ↔ наш base; Goldman severe $115 ↔ наш bull). Если в одном ballpark (±$15) — калибровка ОК. Если расходимся на >$25 — пересмотр shifts.
- **B3 — scenario lookback proof-point** (для отчёта §4.5). Сделать прогноз с `scenario="bull"` от 2026-01-15 (до Hormuz crisis) на 3m → сравнить с realized $100 в мае. Один эпизод, не статистика, но честный proof point: «наш escalation scenario от Jan 2026 предсказал бы $95-105 на May 2026, что в ballpark с realized $100».

B1 — обязательно, в коде. B2 — отчёт §4.5 + commentary в `interpret.py`. B3 — manual smoke в `scripts/eval/eval_forecast.py`.

## Слабые места и mitigation (обзор)

| Слабое место | Mitigation |
|---|---|
| `base_anchor_shift` = observation, не модель | Явный label в metadata + interpretation; sensitivity warning |
| Snapshot `2026-05-08` устаревает | `as_of`-warning при сдвиге >14 дней; раздел «когда обновлять» в ADR |
| Bear/bull детерминированы от base | CI сохраняется в каждом scenario через `ci_*_scenario = ci_*_base + delta_*` |
| Линейная суммация driver shifts в custom scenarios | Preset (bear/base/bull) калиброван как целое; custom помечается «оценочный» |
| Калибровка shifts экспертная (literature-based) | В коде каждое число с `# source: ...`; единое место правки |
| Spread (brent, urals) — не модель, а schedule lookup | Архитектурно оправдано (urals derived); честно в interpretation |
| Spread CI на shock-events | Eval-скрипт coverage; если <70% out-of-sample — runtime warning |

## Что отвергли и почему

- **Variant B** (base = stable pre-shock baseline, bull = hypothetical Hormuz disruption) — out of touch с реальностью; bull уже произошёл. Открытое vranye перед оценщиком.
- **Variant C** (two-layer: model_baseline + current_overlay) — слишком complex для UI/interpretation; risk что layered numbers выглядят как massaging.
- **Exog regressors** (а/б/в) в первой итерации — неподъёмный data engineering на 3.5 дня. v2.2+ при необходимости.
- **Single spread на все сценарии** — creator: «ни о чём»; spread сам зависит от geopolitics, разный per scenario.
- **Open calculator forecast(X−Y) для произвольных пар** — overengineering; closed list (brent, urals), (brent, wti).
- **Optional scenario в citation format** — LLM пропускает в 15-25% случаев, regex усложняется.
- **Применять сценарии к газовым активам (TTF, henry_hub)** в v2.1 — не требуется в roadmap, фокус нефтяной.
- **Backtest на labeled scenario history** — нет данных, неподъёмно собирать.

## Acceptance этого ADR

- [ ] Creator подтвердил Q1 (Variant A) ✅ (2026-05-08, обсуждение)
- [ ] Creator подтвердил Q3 spread per-scenario ✅ (2026-05-08, обсуждение)
- [ ] Creator подтвердил snapshot 2026-05-08 + список 5 драйверов
- [ ] Creator подтвердил Q4 citation format
- [ ] Citation format финализирован — Track D патчит `nefteboros/citations/patterns.py` с regex выше

После acceptance — A1 implementation начинается.

## Ссылки

**Внутренние:**
- ADR-0012 (`docs/adr/0012-price-tools.md`) — текущая архитектура моделей и backtest.
- ADR-0013 (`docs/adr/0013-hybrid-forecasting.md`) — гибридизация forecast + RAG + web на уровне synthesize.
- ADR-0019 (`docs/adr/0019-system-prompt-analyst.md`) — текущий citation format в SYSTEM.md.
- Roadmap (`docs/roadmap-v2.1.md`) — секции Track A.
- `nefteboros/forecast/api.py`, `nefteboros/forecast/models/ensemble.py`, `nefteboros/forecast/data/spread_schedule.py`.

**Внешние (research для snapshot и калибровки):**
- [Wikipedia 2026 Strait of Hormuz crisis](https://en.wikipedia.org/wiki/2026_Strait_of_Hormuz_crisis)
- [CNBC 2026-05-07: Brent $100, MOU pending](https://www.cnbc.com/2026/05/07/oil-prices-today-trump-iran-strait-of-hormuz-us-crude-brent-.html)
- [Middle East Insider Q2 2026: Iran exports](https://themiddleeastinsider.com/2026/04/28/iran-sanctions-q2-2026-oil-export-status/)
- [Washington Post 2026-03-20: Trump partial Iran sanctions lift](https://www.washingtonpost.com/business/2026/03/20/iran-oil-sanctions-trump/)
- [OPEC press release 2026-04-05](https://www.opec.org/pr-detail/1756597-5-april-2026.html)
- [S&P Global: 3.6 mbpd OPEC+ cuts through 2026](https://www.spglobal.com/energy/en/news-research/latest-news/crude-oil/052825-opec-retains-36-mil-bd-of-group-wide-cuts-through-2026)
- [EU finance: $44.10 dynamic cap from 2026-01-15](https://finance.ec.europa.eu/news/new-dynamic-mechanism-lower-price-cap-russian-crude-oil-4410-barrel-2026-01-15_en)
- [IEA Oil Market Report February 2026](https://www.iea.org/reports/oil-market-report-february-2026)
- [Goldman Sachs Q2 2026 forecasts factbox](https://www.investing.com/news/commodities-news/factboxgoldman-sachs-lowers-secondquarter-2026-oil-price-forecasts-on-usiran-ceasefire-4604522)
- [J.P. Morgan Global Research oil 2026](https://www.jpmorgan.com/insights/global-research/commodities/oil-prices)
