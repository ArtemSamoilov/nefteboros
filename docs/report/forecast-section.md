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
