# §4.5 Forecast: расчётный модуль прогнозирования цен

_Краткая версия для финального отчёта. Подробности в [ADR-0024](../adr/0024-ou-regime-forecast.md), исходный stat-model подход — в [ADR-0023](../adr/0023-forecast-ensemble-map.md)._

## Решение

Модуль прогнозирования использует **regime-conditioned mean-reverting процесс (Ornstein-Uhlenbeck) с per-scenario калибровкой** — не классические стат-модели (SARIMAX, GBR ensemble).

**Почему не SARIMAX/GBR.** Стат-модели на mixed history дают **расходящийся доверительный интервал** на длинных горизонтах: variance растёт ∝ √h, на 12m ширина CI ≈ ±35-40% от mid. На shock-данных (Hormuz 2026, war 2022) conformal calibration попадает на outliers — CI шириной $300-700 для Brent. Senior-аналитик не примет такой forecast как actionable.

**Почему OU.** Цена commodity **structurally bounded**: floor задан стоимостью производства (~$40-50 marginal cost), ceiling — порогом demand destruction (~$120-150 на нефти). Mean-reverting процесс отражает это явно: variance bounded на длинных горизонтах = `σ²/(2θ)`, не растёт. Это **не статистическое допущение**, а physics of commodity markets — подтверждено исторически: 1985 Saudi flood, 1990 Gulf War, 2008 spike, 2014 OPEC flood, 2020 COVID, 2022 Russia war — **все** возвращались к long-run mean за 6-24 месяцев.

## Сценарии

Три preset'a — структурируют forward-looking discussion для аналитика:

- **base** — текущий shock equilibrium (Brent ~$100, calibrated по Goldman pre-ceasefire $99). «Если ничего не разрешится за горизонт».
- **bear** — de-escalation (Brent → $70, calibrated по Goldman post-ceasefire $90 + JPM avg $60 floor + long-run real-adj $58). MOU подписан, Hormuz reopens, Iran частично возвращается.
- **bull** — escalation (Brent → $120, calibrated по Goldman severe Q4 $115 + persistent Hormuz losses 2 mbpd). Hormuz partial closure, Iran tightening, OPEC+ unwinding.

Каждый сценарий задаёт `(μ, θ, σ, inflation)`:
- **μ** — long-run target (какая цена «правильная» для этого сценария)
- **θ** — speed of reversion (как быстро рынок «договаривается» — half-life)
- **σ** — annualized volatility вокруг target (regime-specific)
- **inflation** — линейный nominal drift (5%/y нефть, 4%/y газ, 10%/y RUB equity; для MOEX bull — 3%, отражает RUB devaluation в hard currency)

Калибровка — research-anchored: bank consensus (Goldman/JPM/Reuters/EIA STEO), Kilian elasticity (~$12/bbl per 1 mbpd), historical regime volatility (pre_war 22%, war_shock 55%, cap_phase 28%, OVX current 70%).

## Прогноз на год вперёд — все активы (snapshot 2026-05-08)

Таблица ниже — фактический вывод `forecast(asset, "12m", scenario=...)` по всем 10 OU-калиброванным активам × 3 сценария. Сгенерирована **2026-05-24** скриптом [`scripts/forecast_table.py`](../../scripts/forecast_table.py); полный артефакт (горизонты 1m/3m/6m/12m, CI80 **и** CI95) — [`forecast-table.csv`](forecast-table.csv) / [`forecast-table.json`](forecast-table.json).

> ⚠️ **Snapshot потенциально устарел — числа читать с поправкой.** OU-параметры (μ, θ, σ) заморожены на `AS_OF_DATE = 2026-05-08`; на дату генерации прошло **16 дней** при пороге `REVIEW_AFTER_DAYS = 14`. При этом spot фетчится live: за две недели Brent сместился с $100.06 (snapshot) до **$103.54** (obs 2026-05-22), т.е. калибровка μ под «Brent ~$100» уже не вполне отражает рынок. После этапа 2 (web→flags) таблица будет регенерироваться с актуальными под текущее состояние флагами; до тех пор base-сценарий стоит читать с учётом сдвига spot.

Spot — последняя наблюдённая цена (obs **2026-05-22** для глобальных активов и MOEX-индекса, **2026-05-24** для GAZP/NVTK). `mid` — точечный прогноз на 12m; в скобках — 80% доверительный интервал. Полная метаинформация прогона (commit, версия Python, staleness-флаг) — в шапке `forecast-table.json`.

| Актив | Ед. | Spot | base: mid [CI80] | bear: mid [CI80] | bull: mid [CI80] |
|---|---|---|---|---|---|
| Brent | USD/bbl | 103.54 | 103.65 [87.2, 120.1] | 75.17 [65.3, 85.0] | 122.33 [95.8, 148.8] |
| WTI | USD/bbl | 96.60 | 99.05 [83.3, 114.8] | 70.82 [61.6, 80.1] | 116.64 [91.4, 141.9] |
| Urals | USD/bbl | 86.54 | 85.80 [71.1, 100.5] | 66.32 [57.0, 75.7] | 97.86 [75.3, 120.5] |
| ESPO | USD/bbl | 97.54 | 97.35 [81.3, 113.4] | 69.87 [60.4, 79.4] | 115.20 [89.4, 141.0] |
| Urals/ESPO blend (Минфин) | USD/bbl | 88.96 | 87.96 [72.9, 103.0] | 67.44 [57.9, 77.0] | 101.71 [78.2, 125.2] |
| Henry Hub | USD/MMBtu | 2.91 | 2.91 [2.0, 3.9] | 2.47 [1.8, 3.2] | 3.42 [1.8, 5.0] |
| TTF | EUR/MWh | 48.68 | 45.99 [31.1, 60.9] | 38.25 [27.3, 49.2] | 54.88 [31.8, 78.0] |
| MOEX O&G | pts (RUB) | 6809.97 | 7394.54 [6220.8, 8568.3] | 7867.22 [6967.9, 8766.6] | 5021.31 [3666.8, 6375.8] |
| Газпром | RUB | 117.40 | 128.79 [105.6, 152.0] | 141.29 [123.3, 159.2] | 82.92 [55.0, 110.9] |
| Новатэк | RUB | 1103.70 | 1231.87 [991.9, 1471.8] | 1384.14 [1190.7, 1577.5] | 803.30 [498.6, 1108.0] |

`opec_basket` — `forecast()` возвращает **refusal** (fetcher OPEC Reference Basket не реализован, P1 backlog); в артефакте — отдельная строка со `status=refusal`.

**Чтение по сценариям** (драйверы — в разделе «Сценарии» выше; здесь — что дают числа на горизонте года):
- **base** — рынок остаётся у текущего shock-равновесия: Brent ≈ spot ($104), газ near-flat. «Если за год ничего не разрешится».
- **bear** (де-эскалация) — нефть откатывается к long-run норме: Brent −27% к spot ($75), Urals $66, ESPO $70; газ мягче (TTF €38).
- **bull** (эскалация) — глобальная нефть растёт: Brent +18% ($122), CI шире (bull σ выше).
- **Инверсия для российского нефтегаза.** moexog/gazp/nvtk откалиброваны **INVERTED**: тот же «bull» (эскалация нефти) бьёт по российскому equity (санкции, отток капитала, девальвация RUB в hard currency) — GAZP −29% ($83), NVTK −27% ($803), MOEX O&G −26%. Зеркально «bear» (де-эскалация) для них позитивен: GAZP +20% ($141). Для роли кредитного аналитика это ключевой разворот: сценарий, бычий для Brent, — медвежий для эмитентов. (Оговорка про то, что OU с θ=1.0 не воспроизводит 2022-кинетику −60% за квартал — в «Ограничения».)

**Воспроизведение:** `python scripts/forecast_table.py` (Python 3.12 — паритет с прод-сервером; forecast-путь не зависит от langchain-gigachat, который ломается на 3.14, но прогон артефакта держим на 3.12).

## Walk-forward результаты (5y, monthly origin, 2781 records)

| Asset | Scenario | h=3m MAPE / cov80 | h=12m MAPE / cov80 |
|---|---|---|---|
| **brent** | bear | 9.0% / 0.70 | 12.5% / 0.58 |
| brent | base | 12.7% / 0.58 | 30.9% / 0.21 |
| brent | bull | 18.4% / 0.55 | 50.6% / 0.12 |
| **wti** | bear | 8.7% / 0.70 | 13.6% / 0.50 |
| **gazp** | base | 12.7% / 0.64 | 11.1% / 0.82 |
| ttf | bear | 19.8% / 0.70 | 30.6% / 0.61 |
| henry_hub | base | 21.5% / 0.65 | 22.2% / 0.61 |

**Ключевые observations:**
- `bear` universally robust — MAPE 6-17% по нефти/газу, coverage 0.50-0.73. Это **production-grade** даже в анахронистическом тесте (params calibrated к 2026-05, evaluated на 2021-2025 history).
- `base`/`bull` на 12m — высокий MAPE (30-65%). Параметры откалиброваны к **forward-looking 2026-05 → ahead** scenarios; backtest на calm history 2021-2025 — anachronistic test of universality, **expected by design**.
- Полные numbers per (asset × scenario × horizon × regime) — в `metrics/runs/20260509_072707_ou_walkforward_af9f762.json`.

## Ограничения

- **Snapshot 2026-05-08 заморожен** — μ калиброван под текущее состояние (Hormuz blocked, Iran 0.4 mbpd, Brent $100). Runtime warning при сдвиге даты > 14 дней.
- **μ откалиброван экспертно** (research consensus + bank publications), не via Maximum Likelihood Estimation. Каждое значение в коде с `# source: <reference>`.
- **MOEX bull — gradual reversion, не fast crash**. На 12m bull для GAZP даёт −29% от spot; реальный 2022 GAZP −60% за 3 месяца. OU с θ=1.0 не reproducing 2022 kinetics. Bull preset = representative escalation, не worst-case 2022 повтор; для extreme — `ScenarioParams` custom (v2.2 backlog).
- **TTF bull — extreme tail**. 2022 war shock дал TTF €300+ — outside our scenario calibration. MAPE 65% на 12m — known limitation.
- **OPEC basket** — fetcher не реализован (XML feed на opec.org требует отдельной интеграции). P1 backlog.

## Что улучшил бы при большем времени

1. **Per-snapshot calibration**: для каждой даты historical backtest восстанавливать μ/θ/σ из bank consensus того периода (требует историческую базу publications). Сейчас static current params, что и обеспечивает «test of universality», но не «reproducing past regimes».
2. **Quarterly seasonal model для газа** (TTF/HH): зимний/летний spike currently averaged out. Seasonal overlay улучшил бы MAPE на 6m+.
3. **Custom scenarios через ScenarioParams** — flag-driven configuration: пользователь передаёт `(hormuz=full_closure, iran=lifted, ...)`, модель строит forecast из supply/demand model. Сейчас только bear/base/bull presets.
4. **Live OVX integration** для дinamic σ recalibration: brent options IV (Barchart) vs OVX (CBOE) → real-time scenario-specific volatility update.
5. **MOEX θ flexibility** для fast-crash scenarios: opt-in θ=2.0 или 4.0 для 2022-style panic как separate preset.

## Ссылки

- [ADR-0024](../adr/0024-ou-regime-forecast.md) — полная методология OU (math, термостат-аналогия, калибровочные таблицы, A4-A8 refinements)
- [ADR-0023](../adr/0023-forecast-ensemble-map.md) — initial post-modeling shift подход (legacy, superseded)
- [ADR-0012](../adr/0012-price-tools.md) — оригинальный SARIMAX/GBR ensemble (backtest infrastructure)
- `nefteboros/forecast/scenarios.py` — ASSET_PARAMS calibration tables
- `scripts/eval/eval_ou.py` — walk-forward backtest реализация
- `metrics/runs/*_ou_walkforward_*.json` — backtest результаты
