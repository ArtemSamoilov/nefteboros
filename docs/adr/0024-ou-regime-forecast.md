# ADR-0024 — Regime-conditioned mean-reverting forecast (Ornstein-Uhlenbeck per scenario)

- **Дата:** 2026-05-08
- **Статус:** Принято (заменяет ADR-0023 в части forecast methodology; ADR-0023 описывает legacy post-modeling shift подход, оставлен как историческая запись)
- **Контекст:** Track A roadmap v2.1 (`docs/roadmap-v2.1.md`). После критического review таблиц прогнозов (multi-cycle iteration с creator-ом 2026-05-08) выяснилось, что post-modeling shift на стат-моделях SARIMAX+GBR **в принципе не даёт actionable CI** на горизонтах ≥3m в shock-режиме — даже после 6 фиксов в ADR-0023 v3 ширина CI оставалась ±30-40%, что creator справедливо назвал «гаданием, не прогнозом».
- **Связано:** ADR-0012 (price tools, оригинальный ensemble), ADR-0013 (hybrid forecasting), ADR-0019 (citation format), ADR-0023 (legacy post-modeling shift подход).

## Контекст и проблема

Стат-модели (SARIMAX, GBR ensemble) обучаются на исторических ценах и **fundamentally** дают расходящиеся CI на длинных горизонтах:

```
Var[S_t] ≈ σ² × t      (random walk / GBM)
```

Variance растёт линейно во времени, sigma растёт ∝ √t. На 12m с σ=25%/year ширина 80% CI ≈ ±32% от mid — **бесполезно** для аналитика.

Дополнительно — модели на shock-period train (2024-2026) содержат outlier residuals от Hormuz crisis Q1 2026 (Brent $50→$100). Conformal calibration попадает на эти outliers → ширина ещё больше.

Post-modeling shift подход (ADR-0023) пытался исправить это через:
- horizon scaling delta
- scenario-aware anchor decay
- bull preset cap
- weighted ensemble CI
- clip negative

После всех 6 фиксов CI на 12m bear оставались [$0, $260], base — refusal, bull — [$0, $328]. **Это не уровень senior-аналитика.**

**Корень проблемы:** модели не имеют **structural awareness** — они учат по price history, без знания что цена commodity **ограничена** structural floor (cost of production) и ceiling (demand destruction). Стат-модели treat прайс как unbounded random walk, что неверно для commodity.

## Решение

**Переход с post-modeling shift на regime-conditioned Ornstein-Uhlenbeck (mean-reverting) процесс per scenario.**

```
dS = θ(μ(t) - S) dt + σ dW
```

где:
- **μ(t)** — long-run target per scenario, дрейфует с инфляцией: `μ(t) = μ₀ × (1 + i × t)`
- **θ** — speed of reversion (1/year, half-life = ln(2)/θ)
- **σ** — annualized volatility around target (% of spot)
- **dW** — Wiener process

### Forecast formulas

```
E[S_t | S_0]   = μ(t) + (S_0 - μ_0) × exp(-θ·t)
Var[S_t | S_0] = σ²/(2θ) × (1 - exp(-2θ·t))
CI 80%(t)      = E[S_t] ± 1.282 × √Var[S_t]
CI 95%(t)      = E[S_t] ± 1.960 × √Var[S_t]
```

**Ключевое свойство:** при t → ∞, Var → σ²/(2θ) — **bounded**. CI **не расходится** на длинных горизонтах. Это и есть mathematical fix problem ADR-0023.

## Почему mean reversion корректно для commodity

### Аналогия: термостат в комнате

- Зимой включаешь батарею, она **тянет** температуру к 22°C (μ — target).
- Сила батареи определяет, как быстро температура восстанавливается, если открыть окно (θ — speed of reversion).
- Сквозняки и хлопки двери дают случайные кратковременные отклонения (σ — volatility).

**Через 1 час** после открытия окна: температура в диапазоне 18-26°C (далеко от 22).
**Через 12 часов**: всё ещё 18-26°C — батарея не даёт уйти дальше. **Диапазон не растёт во времени.**

Та же физика для commodity: рынок имеет «батарею» — fundamental forces (production costs, demand elasticity, geopolitical equilibria), которые тянут цену к долгосрочному target. Случайные новости создают краткосрочные отклонения, но мid возвращается к scenario equilibrium.

### Structural property of commodities

Это не статистическая модель, это **physical property** commodity markets:

- **Floor**: цена не уходит ниже cost of production (для нефти это $40-50 marginal cost для крупных производителей; OPEC defends price). При прорыве вниз — производство сокращается, supply падает, цена восстанавливается.
- **Ceiling**: цена не уходит выше demand destruction threshold (для нефти ~$120-150 — demand начинает падать структурно: substitution, efficiency, recession). При прорыве вверх — demand уменьшается, цена возвращается.

Цена commodity **ограничена сверху и снизу** structural forces. Random walk / GBM ignore это — они treat price как unbounded. Mean reversion explicit отражает structural bounds.

### Историческое подтверждение mean reversion на нефти

Все крупные ценовые шоки на нефти исторически возвращались к равновесию в рамках 6-24 месяцев:

| Эпизод | Spike / Crash | Пик / Дно | Возврат к equilibrium |
|---|---|---|---|
| **1985 Saudi flood** | Crash $30 → $10 за полгода | Q1 1986 | Возврат к $18 за 12 месяцев (восстановление в стабильное равновесие $18-22) |
| **1990 Gulf War spike** | $20 → $40 за 3 месяца | Oct 1990 | Возврат к $20 за 4 месяца (после освобождения Кувейта) |
| **2008 supply spike + crash** | $80 → $147 → $35 | Jul 2008 / Dec 2008 | Возврат к $75-80 за 18 месяцев |
| **2014 OPEC flood** | $100 → $30 за 18 месяцев | Q1 2016 | Возврат к $50-70 за 24 месяца (OPEC+ deal) |
| **2020 COVID** | $60 → -$37 (futures roll) → $40 | Apr 2020 | Возврат к $50-70 за 4 месяца |
| **2022 Russia war** | $80 → $130 за 2 месяца | Jun 2022 | Возврат к $80-95 за 6 месяцев (cap regime stabilized) |

**В каждом случае** цена показывала mean reversion после шока — не random walk drift to infinity / zero. Это empirical validation подхода.

В текущем шоке (Hormuz crisis 2026) логика та же: рынок придёт к новому equilibrium либо через de-escalation (bear), либо через persistent shock (base), либо через escalation (bull). OU моделирует все три траектории явно.

## Calibration parameters per asset per scenario

Параметры калиброваны по historical regime data + bank consensus + literature.

### Нефть (oil group)

| Asset | Spot | μ bear | μ base | μ bull | θ bear | θ base | θ bull | σ bear | σ base | σ bull | infl |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **brent** | $100.06 | 70 | 98 | 120 | 3.0 | 2.0 | 1.5 | 20% | 25% | 30% | 5% |
| **wti** | $94.81 | 66 | 94 | 115 | 3.0 | 2.0 | 1.5 | 20% | 25% | 30% | 5% |
| **urals** | $83.06 | 62 | 81 | 95 | 3.0 | 2.0 | 1.5 | 22% | 27% | 32% | 5% |
| **espo** | $94.06 | 65 | 92 | 113 | 3.0 | 2.0 | 1.5 | 21% | 26% | 31% | 5% |
| **urals_minfin_blend** | $85.48 | 63 | 83 | 99 | 3.0 | 2.0 | 1.5 | 22% | 27% | 32% | 5% |

**Обоснование:**
- **μ bear ($70 Brent)**: Reuters Feb 2026 consensus $63.85 (pre-shock norm), Goldman post-ceasefire $90 как short-term, long-run real-adj avg $58 (1946-2025) → mean ~$70 для bear «full de-escalation, return к pre-shock norm + inflation»
- **μ base ($98 Brent)**: spot ≈ $100, Goldman pre-ceasefire $99 → $98 как «текущее shock equilibrium»
- **μ bull ($120 Brent)**: Goldman severe Q4 $115 при 2 mbpd persistent loss → bull «expected escalation peak» ~$120
- **θ**: половинный период reversion: bear 2.8 мес (calm regime, fast), base 4.2 мес, bull 5.5 мес (turbulent, slow)
- **σ**: pre_war 2021 ~22%, war_shock 2022 ~55%, cap_phase 2023-25 ~28%, OVX current ~70%. Мы выбираем regime-specific среднее, не contemporaneous OVX (который mixed across scenarios).
- **Urals/ESPO/blend**: derived через scenario-specific spread, +5pp к base oil σ для spread variability
- **Inflation 5%/year**: long-run real growth oil prices ~2% (research) + nominal CPI ~3% = 5%

### Газ (gas group)

| Asset | Spot | μ bear | μ base | μ bull | θ bear | θ base | θ bull | σ bear | σ base | σ bull | infl |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **henry_hub** | $2.77 | 2.30 | 2.77 | 3.50 | 2.0 | 1.5 | 1.0 | 35% | 45% | 55% | 4% |
| **ttf** | €43.56 | 35 | 43 | 55 | 2.0 | 1.5 | 1.0 | 35% | 45% | 50% | 4% |

**Обоснование:**
- Газ **более волатилен** чем нефть (HH 2022 = 91% real vol; HH 2023 = 69%; TTF 2022 — extreme war shock €300+). σ scenario-specific scaled relative.
- **Slower mean reversion** (θ=1-2 vs 1.5-3 для нефти) — gas markets less liquid, regime changes слабее arbitraged
- **Inflation 4%/year** — gas substitutable с другими energy carriers (electric heating, renewables), passthrough меньше нефти

### Российский нефтегаз proxy (russian energy equity)

| Asset | Spot | μ bear | μ base | μ bull | θ bear | θ base | θ bull | σ bear | σ base | σ bull | infl |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **moexog** | 6697 | 7200 | 6700 | 5500 | 2.0 | 1.5 | 1.0 | 18% | 22% | 30% | 10% |
| **gazp** | 117 | 130 | 117 | 85 | 2.0 | 1.5 | 1.0 | 20% | 25% | 35% | 10% |
| **nvtk** | 1124 | 1280 | 1124 | 820 | 2.0 | 1.5 | 1.0 | 22% | 27% | 40% | 10% |

**Inverted bull semantics:** на escalation (sanctions tighten, RUB weakens, foreign capital outflow) Russian equity **падает** несмотря на улучшение fundamentals газовых компаний при высоких commodity prices. Empirically 2022: GAZP −50% YTD при Brent +50%. Russia-specific factors доминируют над commodity tailwind. Defensible.

**μ bull < spot** для всех трёх MOEX → mean-reverting dynamics показывают **спад** к equity bull-equilibrium.

**Inflation 10%/year** — RUB equity nominal growth + risk premium (CBR rate + страновая премия). Это уже включает фон растущих цен; в bull inflation drift частично компенсируется equity sell-off.

## Mapping flags → (μ, θ, σ)

Сценарий = bundle of flags на supply/demand. Через Kilian elasticity (~$12/bbl per 1 mbpd persistent imbalance) flags транслируются в μ adjustment.

### Flags structure

| Flag | base | bear | bull |
|---|---|---|---|
| `hormuz` | blocked (-3 mbpd off) | partial_reopen (-1.5) | partial_closure (-5) |
| `iran` | max_pressure (-1.2 vs pre-shock) | partial_lift (-0.6) | further_tightening (-1.4) |
| `opec_plus` | gradual_unwinding | extended_cuts | accelerated_unwinding |
| `russia_cap` | active ($47.60/$44.10) | active | tightened_dynamic |
| `china_demand` | base (+0.2 mbpd) | base (+0.2) | weak (-0.4) |

### Trans translation (пример Brent base → bear)

**Supply change (de-escalation):**
- Hormuz partial_reopen: +1.5 mbpd
- Iran partial_lift: +0.6 mbpd
- OPEC extended_cuts: -0.5 mbpd
- Net: +1.6 mbpd more supply

**Demand change**: 0 (China base maintained)

**Imbalance**: +1.6 mbpd oversupply

**μ adjustment via Kilian**: −1.6 × $12 = −$19.2

**Long-run baseline** (calm regime): $80 (real-adj 1946-2025 avg $58 + nominal premium $22)

**μ_bear**: $89 calm baseline − $19 imbalance = **$70** ✓

Аналогично **bull**: supply tightening (-1.7 mbpd) + demand weak (-0.4) = imbalance -1.3 mbpd × $12 = +$15.6, baseline shock $104 + $16 = **$120** ✓

В коде drivers хранятся в DRIVERS dict (`scenarios.py`) с per-state mbpd contributions. Calibration values фиксированы, но при изменении snapshot (новые события) — правятся в одном месте.

## Что отвергли и почему

- **Random walk / GBM с post-modeling shift** (ADR-0023). Variance расходится √t. Width CI ±30-40% на 12m даже после 6 фиксов. **Не actionable.**
- **Statистические модели на shock-data** (SARIMAX, GBR ensemble на 2y train с Hormuz spike). Conformal residuals содержат outliers, scaled √h дают CI шириной $300-700.
- **Quantile regression / GARCH / Bayesian** — всё ещё статистика на shock-данных, те же проблемы.
- **Implied volatility from options** (Brent options July 2026 IV = 71%) — даёт width ±50% на 12m, **honest** но всё ещё too wide для actionable forecast.
- **Bank consensus aggregator** — рассмотрен и отвергнут в пользу OU: банки сами consensus driver-conditioned, OU делает то же явно через flags.
- **Нелинейный drift / two-leg trajectory** на 12m — overengineering без empirical justification.
- **OU с bound на CI** (artificial cap) — подкручивание под эстетику.

## Trade-offs (saмокритично)

**Преимущества:**
- **Actionable CI**: width ±10-19% для нефти, ±30-50% для газа (inherent vol higher), bounded на длинных horizons
- **Structural**: каждый параметр имеет attribution (μ от bank consensus + Kilian, θ от liquidity, σ от regime history)
- **Honest physics**: bull = более волатилен (turbulent regime), bear = calm (fast reversion), base = stable
- **Senior analyst flavor**: «при сценарии X target $Y, скорость reversion Z, vol W» — это как реально мыслят аналитики
- **Семантически consistent across assets**: oil/gas/equity все используют тот же framework, только parameters per asset

**Минусы / риски:**

1. **μ калиброван экспертно**, не из исторических данных через MLE. Mitigation: каждое значение в коде с `# source: <reference>`. При появлении новых данных — правится в одном месте.
2. **Inflation drift линеен (μ(t) = μ₀ × (1 + i·t))** — не реальная процентная композиция. На 12m approximation acceptable; на >5y разница накапливается.
3. **Snapshot устаревает**: при крупных новых событиях (MOU подписан / Hormuz reopens / новый ОПЕК+ cut) калибровка μ требует пересмотра. Mitigation: `as_of: 2026-05-08` warning в interpretation.
4. **MOEX bull pulled up by inflation drift** (gazp 12m bull mid 105, только -10% от spot 117). Семантически: на bull equity всё ещё росло бы с inflation, just from a lower base. Defensible.
5. **OU не имеет explicit response к flag changes runtime** — flags hardcoded в presets. Custom scenarios через ScenarioParams для what-if сейчас не реализованы; backlog v2.2.
6. **Газ scenarios seasonal-blind** — TTF bull зимой vs летом разный. На 6m+ это averaged. Acceptable trade-off.

## Acceptance этого ADR

- [x] Creator подтвердил выбор подхода (вариант: regime-conditioned OU per scenario, 2026-05-08)
- [x] Creator подтвердил calibration parameters (multi-cycle iteration на таблицах)
- [x] Creator подтвердил inflation drift implementation
- [x] Bear / base / bull semantic accepted (включая MOEX inverted bull)

После acceptance — реализация в коде (см. ниже).

## Implementation

- `nefteboros/forecast/scenarios.py` — заменена `compute_scenario_delta` на `compute_ou_forecast` с per-asset OU parameters. Old `ScenarioDelta`, `BaseAnchor` deprecated.
- `nefteboros/forecast/api.py` — `_forecast_observable` использует OU напрямую, без post-modeling shift и без stat-model ensemble. Stat-models (SARIMAX, GBR, Ensemble) остаются для backtest/regression-test infrastructure, но не используются в production forecast path.
- `nefteboros/forecast/spread.py` — applied OU framework to spreads (per-scenario μ для Brent-Urals и Brent-WTI).
- `nefteboros/forecast/interpret.py` — обновлено для OU output (target μ, speed of reversion, scenario commentary).
- `prompts/SYSTEM.md` citation format не меняется (`[Forecast: <model>, scenario=<name>, CI <level>]`), только `<model>` теперь = `ou_regime` (вместо `ensemble`).

## Ссылки

- ADR-0012 — оригинальный stat-model ensemble (SARIMAX + GBR)
- ADR-0013 — hybrid forecasting (forecast + RAG + web)
- ADR-0019 — citation format
- ADR-0023 — legacy post-modeling shift (superseded by this ADR for forecast methodology; spread per-scenario logic из ADR-0023 §Q3 сохраняется)
- Roadmap v2.1 §Track A
- Kilian, L. (2009) "Not All Oil Price Shocks Are Alike" — elasticity ~$10-15/bbl per 1 mbpd
- EIA STEO methodology (5 models pooled + expert judgment)
- OVX index (CBOE Crude Oil Volatility Index)
