# 2026-05-08 — Track A: сценарный forecast, spread per-scenario, reproducibility

**PR:** `feature/track-a-forecast-scenarios` (ветка `claude/objective-kapitsa-747781`)
**Связано:** [ADR-0023](../adr/0023-forecast-ensemble-map.md), roadmap v2.1 §Track A.

## Контекст

В v2.0.0 `forecast(asset, horizon)` возвращал точечную оценку с CI 80/95% на endogenous-волатильности 5-летнего окна. Senior-аналитик не говорит «Brent будет $80 ± $8» — он говорит «при базовом сценарии $80, при bear $65–70, при bull $95–110». Track A добавляет сценарный режим, спреды per-scenario и reproducibility-тест.

Дополнительно — реальный snapshot 2026-05-08 показал, что market в **shock-режиме** (Hormuz blocked с 28 февраля, Brent $100, Iran exports collapsed). Roadmap A1 предполагал «base = ОПЕК+ удерживает квоты, Иран в текущем режиме» — это пасторальное состояние из 2024, не текущее. Дизайн сценариев перевёрнут: base = current shock anchored to spot, bear = de-escalation, bull = full Hormuz closure.

## Решение

### Архитектура сценариев (вариант A из обсуждения с creator-ом)

```
forecast(asset, horizon, scenario="base")
  raw_ensemble = SARIMAX+GBR mean output                 # ~$68 (model belief)
  base_anchor_shift = observed_spot - raw_ensemble       # +$32 (observation)
  base_value = raw_ensemble + base_anchor_shift          # ≈ spot

  scenario_delta:
    base → 0
    bear → -$10..-$25 (de-escalation: Hormuz reopens, Iran partial lift)
    bull → +$25..+$75 (escalation: Hormuz fully closed)

  final_value = raw + base_anchor + delta
  final_ci    = original_ci + base_anchor + (delta_low, delta_high)  # asymmetric
```

Калибровка shift'ов — research-verified против Goldman ($90 post-ceasefire / $99 pre-ceasefire / $115 severe), JPM ($60 floor) и Kilian elasticity (~$10–15/bbl per 1 mbpd persistent shift).

### 5 драйверов под реальный snapshot 2026-05-08

| Driver | States | Shift |
|---|---|---|
| `hormuz_status` | `blocked` (current) / `partial_reopen` / `full_reopen` / `full_closure` | 0 / -$15..-$22 / -$30..-$45 / +$50..+$75 |
| `iran_sanctions` | `maximum_pressure_active` (current) / `partial_lift` / `full_lift` / `further_tightening` | 0 / -$6..-$10 / -$12..-$18 / +$2..+$3 |
| `opec_plus_unwinding` | `gradual` (current, +206k bpd/мес) / `accelerated` / `extended_cuts` | 0 / -$10..-$15 / +$5..+$8 |
| `russia_cap_enforcement` | `active` ($47.60 G7 / $44.10 EU dynamic) / `tightened_dynamic` / `removed` | 0 / +$3..+$5 / -$5..-$8 |
| `china_demand` | `weak` / `base_+0.198_mbpd` (current per IEA) / `strong` | -$3..-$5 / 0 / +$3..+$5 |

### Spread per-scenario (creator: «один спред на все сценарии — ни о чём»)

`forecast_spread()` всегда возвращает все три сценария (`bear`/`base`/`bull`) с per-scenario commentary и driver breakdown:

- **(brent, urals)** — schedule-based с per-scenario adjustment. Urals — DERIVED, реальный daily-spot обрезан Feb 2025; spread берётся из `spread_schedule.py` с адаптацией по cap binding logic (bear: cap non-binding → discount narrows; bull: secondary sanctions risk → discount widens).
- **(brent, wti)** — SARIMAX на series_diff (mean-reverting; обе серии реальные daily) + per-scenario shift по US shale isolation logic (bull: ME blocked → US shale relative supply advantage → spread widens).
- Closed list of pairs; любая другая — `ForecastRefusal` с явным объяснением.

### Citation format (для координации с Track D)

`[Forecast: <model>, scenario=<name>, CI <level>]` — scenario обязателен (mandatory), даже для default base. Diagnostic в Langfuse trace и LLM-compliance — main мотивы.

```
[Forecast: ensemble, scenario=base, CI 80%]
[Forecast: ensemble, scenario=bear, CI 80/95%]
[Forecast: sarimax, scenario=bull, CI 95%]
```

### Reproducibility (A3)

`_seed_for_reproducibility()` re-seed numpy и random в начале каждого `forecast()` и `forecast_spread()`. Защита от 3rd-party implicit-random (statsmodels SARIMAX optimizer init) при последовательных вызовах в одной сессии. Маркер `pytest -m network` для full pipeline determinism тестов.

## Изменения

### Новые файлы

- [`docs/adr/0023-forecast-ensemble-map.md`](../adr/0023-forecast-ensemble-map.md) — полная карта решений Track A с research-калибровкой и slabые места + mitigation.
- [`nefteboros/forecast/scenarios.py`](../../nefteboros/forecast/scenarios.py) — типы драйверов, `PRESET_SCENARIOS`, `compute_scenario_delta()`, `compute_base_anchor()`, `parse_scenario()`, `is_scenario_applicable()`, snapshot 2026-05-08.
- [`nefteboros/forecast/spread.py`](../../nefteboros/forecast/spread.py) — `forecast_spread()` для (brent, urals) и (brent, wti) с per-scenario логикой и commentary.
- [`tests/test_forecast_reproducibility.py`](../../tests/test_forecast_reproducibility.py) — 6 unit + 6 network тестов (12/12 passed).

### Изменения

- [`nefteboros/forecast/api.py`](../../nefteboros/forecast/api.py): `forecast()` принимает `scenario`, применяет anchor + delta в `_apply_scenario_shift()`, расширяет metadata (scenario_label, base_anchor_shift, scenario_delta_*), пробрасывает scenario через `_forecast_derived()`. `_seed_for_reproducibility()` в начале каждого вызова.
- [`nefteboros/forecast/schema.py`](../../nefteboros/forecast/schema.py): добавлены `SpreadScenarioEntry` и `SpreadForecastResult`.
- [`nefteboros/forecast/interpret.py`](../../nefteboros/forecast/interpret.py): scenario block (anchor disclosure + driver breakdown), snapshot freshness warning, citation hint в формате ADR-0023.
- [`nefteboros/forecast/__init__.py`](../../nefteboros/forecast/__init__.py): exposed `forecast_spread`, `SpreadForecastResult`, `ScenarioParams`, `PRESET_SCENARIOS`.
- [`prompts/SYSTEM.md`](../../prompts/SYSTEM.md): обновлён citation format на `[Forecast: <model>, scenario=<name>, CI <level>]`; anti-hallucination секция дополнена про сценарии.
- [`README.md`](../../README.md): описание forecast блока — «SARIMAX + GBR ensemble, сценарный режим (bear/base/bull) под current shock 2026-05» и обновлённый citation example.
- [`docs/roadmap-v2.1.md`](../roadmap-v2.1.md): citation format в D5 ссылается на финальный из ADR-0023.
- [`pyproject.toml`](../../pyproject.toml): зарегистрирован `network` маркер для тестов с интернетом.

## Тестирование

```
$ python -m pytest tests/test_forecast_reproducibility.py -v
6 passed, 6 deselected in 0.45s        # unit (без сетки)

$ python -m pytest tests/test_forecast_reproducibility.py -v -m network
6 passed, 6 deselected in 10.13s        # network (полный pipeline)
```

### Smoke (manual, 2026-05-08)

```
forecast('brent', '3m', scenario='base'): $100.06 (anchor +$11.91, delta 0)
forecast('brent', '3m', scenario='bear'): $80.56  (delta -$19.50)
forecast('brent', '3m', scenario='bull'): $164.56 (delta +$64.50)
direction sanity: bear < base < bull  PASS

forecast_spread('brent', 'urals', '3m'):
  base: $17.00 / bear: $8.50 / bull: $25.00  (schedule-based)
  direction sanity: bear < base < bull  PASS

forecast_spread('brent', 'wti', '3m'):
  base: $5.17 / bear: $3.17 / bull: $8.67  (SARIMAX series_diff)
  direction sanity: bear < base < bull  PASS

forecast_spread('brent', 'ttf', '3m'):
  ForecastRefusal — pair вне closed list  PASS
```

### Калибровка против bank scenarios

| Сценарий | Наш | Goldman | JPM |
|---|---|---|---|
| bear (de-escalation) | $80 | $90 (post-ceasefire) | $60 (avg 2026) |
| base (current shock) | $100 | $99 (pre-ceasefire) | — |
| bull (full closure) | $164 | $115 (severe, 2 mbpd loss) | — |

Bull у нас агрессивнее Goldman severe — отражает full closure (>2 mbpd persistent), не просто 2 mbpd loss. Объяснено в ADR.

## Известные ограничения (для отчёта §4.5)

1. **Snapshot 2026-05-08 заморожен** — при крупных событиях (MOU подписан/отменён, Hormuz reopens) калибровка устаревает. Runtime warning при `today - as_of > 14 дней`.
2. **`base_anchor_shift` — observation, не модель.** Явно labeled в metadata + interpretation.
3. **Газовые активы (TTF, henry_hub) и russian energy proxy (moexog/gazp/nvtk) — scenario не применяется в v2.1.** Возвращается model output без shift с runtime notice.
4. **Russia cap level**: $47.60 (G7) или $44.10 (EU dynamic) — два источника, не уточнено какой реально применяется. Не блокер для калибровки shifts (оба активны).
5. **Scenario validation** — backtest на исторических shocks невозможен (нет labeled scenario data); используется direction sanity (B1) и calibration alignment с bank scenarios (B2). Lookback proof-point на Jan 2026 → realized $100 May (B3) — отложено в backlog для отчёта §4.5.

## Координация с другими треками

- **Track D (D6 — citation patterns + validator).** Финальный regex для forecast citations:
  ```python
  FORECAST_CITE_RE = re.compile(
      r"\[Forecast:\s*(?P<model>[a-z_]+),\s*"
      r"scenario=(?P<scenario>[a-z_]+),\s*"
      r"CI\s*(?P<ci>\d{2}(?:/\d{2})?%)\]"
  )
  ```
  Любые изменения формата — Track A напишет D-сессию, не молча.
- **Track F (Langfuse).** scenario_label в metadata доступен для tracing узла `forecast_call`.

## Не в этом PR (явно)

- **Backlog: scenario для газовых активов** (TTF, henry_hub) — в v2.2+.
- **Backlog: `eval_spread.py` walk-forward сравнение Schema A vs B для (brent, wti).** Сейчас зашита Schema A (SARIMAX на series_diff); experiment как artifact для отчёта §4.5 при наличии времени.
- **Backlog: B3 lookback proof-point**, scenario forecast от Jan 2026 → realized $100 May 2026.
