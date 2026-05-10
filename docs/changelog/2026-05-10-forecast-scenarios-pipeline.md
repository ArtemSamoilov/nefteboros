# 2026-05-10 — forecast scenarios pipeline (bear/base/bull)

## Задача

В контентном плане задача #1: «Сценарный прогноз цен (bear/base/bull) — 3 сценария с явными драйверами, расширить tool: forecast(horizon, scenario={...})».

Track A (v2.1, ADR-0023/0024) сделал API: `forecast(asset, horizon, scenario=...)` поддерживает `'base' | 'bear' | 'bull' | ScenarioParams`. Однако **graph pipeline эту возможность не использовал**:

- `Intent` schema не имела поля `forecast_scenarios`.
- `classify_intent` не детектировал scenario-триггеры в query.
- `forecast_call` node вызывал `forecast(asset, horizon, method=...)` **без** `scenario` — всегда default `'base'`.

В диагностическом WS smoke 2026-05-10 модель честно признавалась: «Bear/bull — аналитический overlay, а не прямые выходы forecast tool». Pipeline закрыт этим PR.

## Что сделано

### 1. `nefteboros/graphs/state.py` — Intent schema

Добавлено поле:
```python
forecast_scenarios: list[str] = Field(default_factory=lambda: ["base"])
```

Backward-compat: default `["base"]` для всех existing call sites.

### 2. `nefteboros/graphs/intents.py` — scenario detection

Новый regex-набор `_SCENARIO_TRIGGERS`:

- `сценари(й|и|ев)`, `bear`, `bull`
- `стресс[-\s]?тест`, `худш(ий|его) случай`, `лучш(ий|его) случай`
- `оптимистичн`, `пессимистичн`
- `разбивк(а|и) по сценари`, `медвеж(ий|и)`, `быч(ий|и)`

Helper `_extract_scenarios(query)` возвращает `['bear','base','bull']` при матче (порядок: худший→центральный→лучший для UX), иначе `['base']`.

Поле `forecast_scenarios=scenarios` прокинуто во все 7 forecast Intent constructors (rules WTI/Brent/Urals/TTF/Henry Hub/generic gas/generic oil).

### 3. `nefteboros/graphs/nodes/forecast.py` — N×M loop

Двойной цикл `for asset × for scenario`: для каждой пары вызывается `forecast(asset, horizon, scenario=scenario, method=...)`. Ошибки одной пары не блокируют остальные. Multi-scenario brent → 3 результата в `state.forecast_results`.

### 4. `nefteboros/prompts/synthesize_forecast_only.md` — section structure + dual citation

Prompt обновлён:

- Пункт 2: «Scenario (`metadata.scenario_label`)» добавлен в список того, что писать про каждый ForecastResult.
- **Новый пункт 4: Multi-scenario ответ** — структура `### {Asset} — сценарный прогноз` с под-секциями Bear/Base/Bull + сводная таблица. Drivers — из `metadata.scenario_params.drivers` или явное «not in metadata, не выдумываю».
- Пункт 8 (citations): для каждого ForecastResult — **две формы**: primary `[Forecast: <method>, scenario=<label>, CI 80/95%]` (D6 spec) + legacy `[forecast_model:<asset>@<horizon>, <method>, ADR-0024]`.

### 5. `tests/test_intent_classifier.py`

3 новых test-блока:

- `test_scenario_triggers_yield_three_scenarios` (parametrize × 6 queries).
- `test_no_scenario_triggers_default_base_only` (parametrize × 4 queries).
- `test_scenario_triggers_dont_change_intent_type` — проверяет orthogonality: scenario-триггер не ломает asset-routing.

## Что НЕ в PR

- **`metadata.scenario_params.drivers`** — поле явных драйверов сценария (Hormuz, Iran exports, OPEC+ stance) пока не добавлено в `ScenarioParams`. Synthesize-LLM честно признаёт отсутствие. Расширение в **отдельный PR** через `nefteboros/forecast/scenarios.py`.
- **`_build_citations` в synthesize node** не менял — citations metadata in state используются validate_citations отдельно. Inline citations LLM пишет через prompt.
- **Re-baseline 100 диалогов через WSRunner** — отложен, реальное качество подтверждено через manual review 6 диалогов в diagnostic-сессии.

## Smoke verification

End-to-end graph test (locally в prod-контейнере, до build/deploy):

```
=== INTENT ===
type=forecast_simple
scenarios=['bear', 'base', 'bull']
matched_rule=rule_1_brent_explicit

=== FORECAST_RESULTS ===
  brent/bear: 85.66 CI80=(75.78, 95.53)
  brent/base: 101.22 CI80=(88.32, 114.12)
  brent/bull: 108.64 CI80=(91.12, 126.16)

=== SYNTHESIS (sample) ===
### Brent — сценарный прогноз на 3 мес
Метод: regime-conditioned mean-reverting OU (`ou_regime`).

**Bear (de-escalation)** • Точка: 85.66 USD/bbl • CI 80%: [75.78; 95.53] ...
**Base (shock equilibrium)** • Точка: 101.22 USD/bbl • CI 80%: [88.32; 114.12] ...
**Bull (escalation)** • Точка: 108.64 USD/bbl • CI 80%: [91.12; 126.16] ...

| Сценарий | Точка, USD/bbl | CI 80%, USD/bbl | CI 95%, USD/bbl |
| Bear | 85.66 | [75.78; 95.53] | [70.55; 100.76] |
| Base | 101.22 | [88.32; 114.12] | [81.50; 120.94] |
| Bull | 108.64 | [91.12; 126.16] | [81.85; 135.43] |

[Forecast: ou_regime, scenario=bear, CI 80/95%]
[forecast_model:brent@3m, ou_regime, ADR-0024]
[Forecast: ou_regime, scenario=base, CI 80/95%]
...
```

Также 8/8 ad-hoc cases в `classify_intent` (включая регрессию на default `['base']`).

## Связанные

- ADR-0023 — forecast ensemble map (предыдущий шаг scenario API).
- ADR-0024 — regime-conditioned OU per scenario (production model).
- Track A (PR #42, #44, #45, #46) — построил forecast(scenario=) API, но не подключил к graph.
- Diagnostic session 2026-05-10 (PR #53/54 release pipeline) — нашла gap через manual review WS-traces.
