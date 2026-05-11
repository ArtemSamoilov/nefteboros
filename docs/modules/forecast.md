# Модуль: Forecast (Brent + спреды, OU regime)

Прогноз цен Brent / WTI / Urals / ESPO / Henry Hub / TTF / MOEX-equity на горизонтах 1m / 3m / 6m / 12m в трёх сценариях (`bear` / `base` / `bull`). Production-метод — **regime-conditioned Ornstein-Uhlenbeck** (mean-reverting per scenario), а не SARIMAX / GBR.

## Точка входа

- `nefteboros/forecast/api.py:75` — `forecast(asset, horizon, *, scenario=None) -> ForecastResult | ForecastRefusal`.
- `nefteboros/forecast/spread.py` — `forecast_spread(asset_a, asset_b, horizon)` для пар `(brent, urals)`, `(brent, wti)`.
- Узел графа: `nefteboros/graphs/nodes/forecast.py:31` — `forecast_call(state)` (вызывает `api.forecast`).
- Skill-level tool entry: `skills/neftegaz_analyst/plugin.py:233` (`analyst_query`) — через граф; либо `forecast_skill` напрямую ([ADR-0016 forecast-skill](../adr/0016-forecast-skill.md)).

## Поток (OU production path)

1. Парс `horizon` (`1m`/`3m`/`6m`/`12m`, ≥18m → refusal).
2. Парс `scenario` (None|`"base"`|`"bear"`|`"bull"`|`ScenarioParams`).
3. Lookup asset в registry, проверка `is_scenario_applicable`.
4. Fetch spot (последняя observed price) — `data/yf.py`, `data/eia.py`, `data/moex.py`.
5. `scenarios.get_ou_params(asset, scenario)` — параметры `(μ, θ, σ, infl)`.
6. `compute_ou_forecast(spot, params, horizon)` — mid + CI 80/95 по формуле OU.
7. `interpret.generate_interpretation` — текстовое объяснение + citation hint `[Forecast: ou_regime, scenario=<name>, CI 80%]`.

Stat-models (`SARIMAX`, `GBR`, `Ensemble`, `RandomWalk` в `models/`) сохранены **только для backtest infrastructure** (regression testing), не для production. См. `api.py:17-19`.

## Входы / выходы

**Вход:** `asset` (один из `ASSET_PARAMS`), `horizon`, optional `scenario`.

**Выход:** `ForecastResult`:
- `end_point: ForecastPoint(value, ci_80, ci_95)`
- `path: list[ForecastPoint]` — траектория mid + CI на промежуточных точках
- `metadata`: `scenario`, `as_of`, `model_method = "ou_regime"`, …
- `interpretation: str` — текст для синтеза.

или `ForecastRefusal` (horizon вне области, asset не applicable, …).

## Ключевые ADR

- [ADR-0012](../adr/0012-price-tools.md) — оригинальная архитектура tools.
- [ADR-0013](../adr/0013-hybrid-forecasting.md) — гибрид forecast + RAG + web.
- [ADR-0023](../adr/0023-forecast-ensemble-map.md) — **legacy** post-modeling shift (заменён ADR-0024).
- [ADR-0024 (OU regime)](../adr/0024-ou-regime-forecast.md) — **production**: regime-conditioned OU per scenario. Описывает калибровку μ/θ/σ per asset, walk-forward backtest, A4-A8 refinements.
- [ADR-0016 (forecast-skill)](../adr/0016-forecast-skill.md) — выделение forecast в skill tool. **Коллизия номера:** есть второй `0016-embed-retrieve.md`. Сигнал для координатора.

## Почему OU, а не SARIMAX/GBR

Подробно — в [ADR-0024](../adr/0024-ou-regime-forecast.md). Кратко: стат-модели на 5y train treat price как unbounded random walk, дают расходящиеся CI (`Var ~ σ²t`, width ±30-40% на 12m даже после 6 фиксов в ADR-0023). OU имеет bounded variance `σ²/(2θ)(1-e^{-2θt})` — на длинных горизонтах CI не разъезжается. Это и есть structural property commodity markets (cost-of-production floor + demand-destruction ceiling).

## Метрики

**Инструментация в production forecast path — НЕТ** в самих моделях / api.py. Span `forecast_call` от узла графа (`analyst_graph.py:126`) фиксирует входы/выход и латентность всего вызова, но **не разбивает** на data fetch / OU compute / interpretation.

Сигнал для координатора: если оценщик попросит latency-breakdown OU vs data fetch — нужно добавить под-spans в `forecast/api.py:_forecast_observable` и `data/*.py`. Сейчас это монолитный span.

**Eval скрипты:**

| Скрипт | Что измеряет | Output |
|---|---|---|
| `scripts/eval/eval_ou.py` | Walk-forward OU backtest: MAPE, Bias, Coverage 80%/95% per `(asset, scenario, horizon)`. Per-regime breakdown (PRE_2022, RUSSIA_WAR_SHOCK, CAP_NORMALIZATION, IRAN_2026). | `metrics/runs/<ts>_ou_walkforward_<sha>.json` |
| `scripts/eval/eval_forecast.py` | Backtest stat-моделей (SARIMAX, GBR, RW, Ensemble) — для baseline сравнения. MAPE, RMSE, coverage_80, coverage_95, MASE vs RW, directional accuracy. | `metrics/runs/<date>_forecast_<sha>.json` |

Полные числа OU backtest (5y, n≈33 origin per cell) — в ADR-0024 §A5. Главное: bear universally robust (MAPE 6-15% на нефть); base/bull на 12m имеют MAPE 30-65% — это **expected**, потому что параметры откалиброваны под 2026-05 shock, а исторический backtest проходит через mixed regimes (анахронистический тест).

## Известные ограничения

- Калибровка `μ` экспертная (bank consensus + Kilian elasticity), не MLE — см. ADR-0024 §«Trade-offs».
- Snapshot `as_of: 2026-05-08` — при сдвиге >14 дней рантайм-warning в interpretation. При крупных новых событиях (Hormuz reopens, MOU подписан) калибровка требует пересмотра.
- Bull preset для MOEX equity = repeat 2022 panic (GAZP −60%); если новая escalation мягче, bear будет точнее. См. ADR-0024 §A6.
- `OU не имеет explicit response к flag changes runtime` — flags hardcoded в presets. Custom `ScenarioParams` для what-if в v2.2 backlog.
