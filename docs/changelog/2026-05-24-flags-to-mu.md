# 2026-05-24 — Геополитические флаги как реальный детерминированный вход μ

**PR:** `feature/forecast-flags-input`
**Связано:** [ADR-0025](../adr/0025-flags-to-mu.md), [ADR-0023 §Q2](../adr/0023-forecast-ensemble-map.md), [ADR-0024 §Mapping](../adr/0024-ou-regime-forecast.md).

## Задача

Сделать геополитические флаги (hormuz/iran/opec_plus/russia_cap/china_demand)
**реальным детерминированным входом** прогноза цен нефти. До этого они были
декоративны: жили в `FLAGS_DECOMPOSITION` только как текст, а `api.forecast()`
брал готовое число `μ_0` из `ASSET_PARAMS` — прогноз не реагировал на смену
обстановки.

## Контекст

Цепочка `состояния флагов → Σ Δmbpd → ×$12 (Kilian) → Δμ` доказана в ADR-0023 §Q2
+ ADR-0024 §Mapping, но в коде её не было. При попытке записать её единой формулой
вскрылось количественное противоречие калибровки ($15 / 1.27 mbpd между bear- и
bull-baseline'ами ADR-0024 + внутренний конфликт ADR-0024 по bull-hormuz: таблица
−5 vs проза −1.7). Ground truth — числа в `ASSET_PARAMS`; baseline/Δ подогнаны под
сходимость, выбор задокументирован в ADR-0025.

## Что сделано

**Код:**
- `nefteboros/forecast/scenarios.py`:
  - `KILIAN_USD_PER_MBPD = 12.0`, `CALM_BASELINE_BRENT = 89.2` — именованные
    константы с `# source`.
  - `DRIVERS` — таблица `{driver: {state: Δmbpd}}`, числа из ADR-0023 §Q2, каждое
    с `# source`. bull-hormuz `partial_closure = −3.27` калибровано под сходимость
    (разрешает внутреннее противоречие ADR-0024).
  - `OIL_ASSETS`, `DRIVER_BASE_STATES`, `FLAG_PRESETS`, `_DERIVED_OIL_AFFINE`.
  - `supply_balance_from_flags()`, `compute_mu_from_flags()`,
    `ou_params_with_flag_mu()`.
- `nefteboros/forecast/api.py`: `forecast()` принимает keyword-only `flag_states`
  (default None). None → замороженные μ (поведение НЕ меняется). Задан → μ через
  цепочку, θ/σ из scenario. Non-oil + flag_states → `ForecastRefusal`. Flag-
  диагностика в `metadata`.

**Тесты** (`tests/test_forecast_flags.py`, 15 unit + 3 network):
- Регресс сходимости: пресеты base/bear/bull воспроизводят `ASSET_PARAMS` μ
  (bear/base точно, bull ≤$0.04, tol $0.1).
- Реакция на hormuz: blocked→reopened ⇒ μ падает + 12m forecast едет вниз; ladder
  монотонен; full_closure ⇒ μ растёт.
- Инвариант bear<base<bull на flag-computed μ и на 12m forecast (все 5 нефтей).
- Валидация (unknown driver/state, non-oil refusal, partial flags → base),
  обратная совместимость `get_ou_params`.

**Docs:** ADR-0025 (цепочка, восстановление baseline, разрешение противоречия
$89/$104, почему детерминированно а не LLM, ограничение нефтью, known limitations),
этот changelog.

## Восстановление μ (фактический прогон)

| asset | bear | base | bull | max \|diff\| |
|---|---:|---:|---:|---:|
| brent | 70.000 | 98.000 | 120.040 | 0.040 |
| wti | 66.000 | 94.000 | 115.039 | 0.039 |
| urals | 62.000 | 81.000 | 95.026 | 0.026 |
| espo | 65.000 | 92.000 | 113.038 | 0.038 |
| urals_minfin_blend | 63.000 | 83.000 | 99.029 | 0.029 |

## Файлы

- **Добавлено:** `docs/adr/0025-flags-to-mu.md`, `tests/test_forecast_flags.py`,
  `docs/changelog/2026-05-24-flags-to-mu.md`.
- **Изменено:** `nefteboros/forecast/scenarios.py`, `nefteboros/forecast/api.py`.
- **Удалено:** —

## Тесты

Python 3.12.12 (`.venv312`, dev/prod parity), точные числа:

| Прогон | Результат |
|---|---|
| `pytest tests/test_forecast_flags.py -m "not network"` | 15 passed, 3 deselected |
| `+ test_ou_sigma_anchor.py + test_forecast_reproducibility.py -m "not network"` | 28 passed, 9 deselected (без регрессий) |
| `pytest tests/test_forecast_flags.py -m network` | 3 passed, 15 deselected |

- Новый файл `test_forecast_flags.py` = 18 тестов (15 unit + 3 network).
- AST-parse `scenarios.py`, `api.py`, `test_forecast_flags.py` — OK.
- Network-тесты (`-m network`) — forecast() с flag_states end-to-end. Spot отдан из
  локального кеша (`use_cache=True`); Yahoo на live-запрос даёт 429 (rate-limit),
  кеш делает прогон детерминированным и независимым от лимита.

## Что НЕ в PR (отложено явно)

- **Классификация состояний флагов из новостей** (новости → state) — этап 2,
  отдельный воркер. Здесь только `state → μ`.
- **Калибровка θ/σ под флаги** — v1 двигает только μ.
- **Газ/equity flag-логика** — другая driver-семантика (inverted bull, seasonal),
  сохраняют ручную калибровку.
- **Непрерывная flag→μ поверхность** (устранение разрыва на стыке base) — backlog.

## Слабые места (саморазгром)

- **Разрыв на стыке base.** base anchored к $98, calm-цепочка — на $89.2; малое
  отклонение от base-набора даёт скачок μ. Артефакт особого случая base (его
  μ привязана к споту, не к calm). Для 3 пресетов и реакции на флаги
  несущественен; непрерывная поверхность — backlog. Документировано в ADR-0025
  §Known limitations.
- **bull-флаги отходят от мягкого −1.3 ADR-0024** к −2.57 (single-baseline
  reconciliation). Сознательный выбор: bear (headline-проверка) оставлен точным,
  $15-противоречие поглощено в bull-hormuz, который у самого ADR-0024
  противоречив (−5 таблица vs −1.7 проза). Альтернатива (два baseline'а) не даёт
  флагам быть входом для произвольных комбинаций.
- **Аффинная карта производных — фит, не вывод из спред-модели.** α,β подогнаны
  под 2 точки (bear/bull); физически интерпретируемы (α<1 = дисконт ширится с
  ценой), но это калибровка, не структурная модель спреда.
