# ADR-0025 — Геополитические флаги как РЕАЛЬНЫЙ детерминированный вход μ прогноза

- **Дата:** 2026-05-24
- **Статус:** Принято
- **Контекст:** Форкаст идёт через Ornstein-Uhlenbeck per scenario (ADR-0024). До
  этого ADR геополитические флаги (hormuz/iran/opec_plus/russia_cap/china_demand)
  жили в `FLAGS_DECOMPOSITION` (`scenarios.py`) **только как текст для
  интерпретации** — в расчёт μ они НЕ входили. `api.forecast()` брал готовое
  число `μ_0` из `ASSET_PARAMS`, `FLAGS_DECOMPOSITION` там не импортировался.
  Следствие: прогноз **не реагировал на смену обстановки** — изменить состояние
  Hormuz нельзя было ничем, кроме ручной правки `ASSET_PARAMS`.
- **Связано:** ADR-0023 §Q2 (каталог драйверов, mbpd/\$ калибровка), ADR-0024
  §«Mapping flags → (μ,θ,σ)» (Kilian-цепочка, OU-параметры), ADR-0012/0013.

## Проблема

`ASSET_PARAMS[asset][scenario].mu_0` — захардкоженные числа, замороженные на
`AS_OF_DATE = 2026-05-08`. Цепочка, которая эти числа **породила**, описана в
ADR-0023 §Q2 + ADR-0024 §Mapping, но в коде её не было:

```
состояния флагов → Σ Δmbpd (таблица DRIVERS) → × (−$12/bbl, Kilian) → Δμ
```

Headline-проверка валидности (Brent, bear):

```
hormuz partial_reopen (+1.5) + iran partial_lift (+0.6) + opec extended (−0.5)
  = +1.6 mbpd профицита
  × (−$12)  = −$19.2
calm_baseline $89.2 − $19.2 = $70.0  =  μ_bear(brent) в коде ✓
```

Цепочка сходится — значит метод верен, просто «заморожен» в готовых числах. Задача
ADR — **восстановить цепочку в коде** так, чтобы флаги стали настоящим входом μ,
а дефолтное поведение `forecast()` (без флагов) не изменилось.

## Решение

Детерминированная функция `compute_mu_from_flags(asset, flag_states) → μ_0` в
`scenarios.py`. Число μ считает **формула из экспертной таблицы** — LLM здесь НЕ
участвует (классификация состояний флагов из новостей — отдельный этап/воркер;
см. §Non-goals).

```
μ_brent(flags) = CALM_BASELINE_BRENT − KILIAN_USD_PER_MBPD · Σ Δmbpd(flags)
μ_derived(flags) = α_asset · μ_brent(flags) + β_asset      (wti/urals/espo/blend)
μ_base = ASSET_PARAMS[asset]["base"].mu_0                   (anchored, особый случай)
```

`forecast(asset, horizon, *, scenario=None, flag_states=None, ...)`:
- `flag_states=None` (default) → `get_ou_params()` как прежде → **замороженные μ**
  (snapshot 2026-05-08). **Обратная совместимость гарантирована.**
- `flag_states={driver: state}` → μ пересчитывается цепочкой; θ/σ/inflation
  остаются из scenario-пресета (флаги в v1 двигают **только μ**).

## Восстановление baseline и разрешение противоречия калибровки

ADR-0024 §Mapping выводит μ из цепочки, но при попытке записать её **единой
формулой** вскрывается противоречие: у bear и bull **разные baseline'ы**.

```
bear:  $89 − 1.6·$12 = $70     (baseline 89)
bull:  $104 + 1.3·$12 = $120   (baseline 104)
```

Разница baseline'ов **$15** — это «затычка» под недостающий размах supply. Спред
bear→bull = $50; при Kilian $12 это требует **4.17 mbpd** размаха, а флаги
ADR-0024 дают только **2.9 mbpd** (+1.6 vs −1.3). Не хватает **1.27 mbpd** ⇒
автор поглотил это скачком baseline ($104 − $89 = $15 ≈ 1.27·$12). То есть
«единый calm_baseline + Σ·$12» как чистая физика **невозможен** — числа
переопределены и взаимно несогласованы (ground truth — числа в `ASSET_PARAMS`).

Дополнительно ADR-0024 §Mapping **сам себе противоречит** по bull-hormuz: таблица
флагов даёт `partial_closure (−5)`, а проза перевода — supply tightening `−1.7`
(что соответствует hormuz ≈ −2).

**Выбор (single-baseline reconciliation):**

1. **Единый `CALM_BASELINE_BRENT = 89.2`** (точно `μ_bear + 1.6·12`; ADR/ТЗ
   округляют до $89). bear оставлен **верным** — код буквально воспроизводит
   headline-проверку.
2. Противоречие $15 поглощено в **bull-hormuz**: `partial_closure = −3.27 mbpd`.
   Это **между** −2 (проза ADR-0024) и −5 (таблица ADR-0024) — то есть выбор
   **разрешает внутреннее противоречие самого ADR** в сторону сходимости, а не
   выдумывает новое число. → bull Σ = −2.57 mbpd → μ_bull = $120.04.
3. **base — особый случай, anchored** к текущему equilibrium/споту (μ_base из
   `ASSET_PARAMS`), НЕ «calm − дельты»: `Σ=0 ⇒ возврат замороженной μ_base`.
   Семантически base ($98) — текущий shock-режим (Hormuz blocked), он выше
   calm-якоря ($89.2); bear де-эскалация уводит ниже calm к pre-shock norm ($70).

`CALM_BASELINE_BRENT` — **фитируемый якорь** под сходимость bear, а НЕ независимо
выведенная «цена спокойного рынка» (bear $70 ниже неё). Это честно отражено в
docstring константы.

### Почему производные нефти — аффинно от Brent, а не своя mbpd-цепочка

Глобальный supply-шок один на весь рынок ⇒ Σ Δmbpd общий. Но если применить
**единый** Σ ко всем нефтям через их calm_baseline, bull не сходится для
urals/blend: их диапазон bull−bear **сжат** (urals 33 vs brent 50), потому что
санкционный дисконт **ширится с ценой** ($8 в bear → $25 в bull). Линейная
mbpd-цепочка с одним Σ и одной эластичностью эту асимметрию не ловит.

Физически корректно: цепочка гонит **Brent** (глобальный бенчмарк), а
urals/wti/espo/blend — дифференциалы от него. Реализовано аффинной картой
`μ_asset = α·μ_brent + β`, подогнанной под (bear, bull) замороженные μ:

| asset | α | β | смысл |
|---|---:|---:|---|
| wti | 0.98 | −2.6 | ~$5 premium, почти параллельно Brent |
| urals | 0.66 | 15.8 | α<1 ⇒ дисконт ширится с ценой (8→25) |
| espo | 0.96 | −2.2 | Asian premium |
| urals_minfin_blend | 0.72 | 12.6 | ≈ 0.78·urals + 0.22·espo (Минфин НДПИ) |

base для производных — тоже anchored (в аффинной карте не участвует).

### Регресс сходимости (фактический прогон)

`compute_mu_from_flags(asset, FLAG_PRESETS[scen])` vs `ASSET_PARAMS[asset][scen].mu_0`:

| asset | bear | base | bull | max \|diff\| |
|---|---:|---:|---:|---:|
| brent | 70.000 | 98.000 | 120.040 | 0.040 |
| wti | 66.000 | 94.000 | 115.039 | 0.039 |
| urals | 62.000 | 81.000 | 95.026 | 0.026 |
| espo | 65.000 | 92.000 | 113.038 | 0.038 |
| urals_minfin_blend | 63.000 | 83.000 | 99.029 | 0.029 |

**bear/base — точно** (0.000); **bull — ≤$0.04** (округление single-baseline). Тест
сходимости — `tests/test_forecast_flags.py::TestFlagChainConvergence` (tol $0.1).

## Почему число детерминированно, а не от LLM

- **Воспроизводимость и аудит.** μ — функция таблицы; одинаковый вход всегда даёт
  одинаковый выход (важно для backtest A3 и Langfuse-диагностики). LLM внёс бы
  стохастику и нерасшифровываемые скачки.
- **Разделение ответственности.** LLM хорош в *классификации* нечёткого ввода
  («что сейчас с Hormuz?» из новостей → `partial_reopen`) — это **этап 2**,
  отдельный воркер. Перевод *состояния* в *число* — экспертная таблица (Kilian,
  bank consensus), не языковая модель.
- **Защитимость на собеседовании.** «μ_bear = $89.2 − 1.6 mbpd · $12 Kilian»
  объяснимо и проверяемо; «LLM сказал $70» — нет.

## Non-goals (явно вне scope v1)

- **НЕ веб / НЕ классификация флагов из новостей** — этап 2, отдельный воркер.
  Здесь только `state → μ`, не `новости → state`.
- **НЕ трогаем θ/σ** — флаги в v1 двигают только μ (long-run target). Bear-подобный
  набор флагов с `scenario="base"` использует base θ/σ. Калибровка θ/σ под флаги —
  backlog.
- **НЕ газ (henry_hub/ttf) и НЕ equity (moexog/gazp/nvtk)** — у них другая
  driver-логика (inverted bull для equity, seasonal для газа). Сохраняют ручную
  калибровку `ASSET_PARAMS`; `flag_states` для них → `ForecastRefusal`.
- **НЕ меняем дефолт `forecast()`** — `flag_states=None` ⇒ замороженные μ.

## Known limitations

1. **Разрыв на стыке base.** base anchored к $98, а calm-цепочка живёт на $89.2.
   Малое отклонение флагов от base-набора даёт скачок μ (98 → ~89−ε). Артефакт
   особого случая base; для 3 пресетов и реакции на флаги несущественен. Непрерывная
   flag→μ поверхность — backlog (вместе с этапом 2).
2. **bull-флаги отходят от мягкого −1.3 ADR-0024** к −2.57 (single-baseline
   reconciliation). Документировано выше; bear (headline) оставлен точным.
3. **Snapshot устаревает.** При крупных событиях (MOU подписан, Hormuz reopens)
   и `CALM_BASELINE_BRENT`, и anchored μ_base требуют пересмотра (как и в ADR-0024).

## Что отвергли

- **Два baseline'а (89 и 104), верные дельты** — воспроизводит μ, но baseline
  back-solved per scenario ⇒ «цепочка» косметическая (хранит ответ), и НЕ работает
  для произвольных наборов флагов (у custom-комбинации нет своего baseline). Не
  даёт флагам быть настоящим входом.
- **Якорь на base (μ_base) + дельты-от-base при Kilian $12** — даёт bear $79 /
  bull $114 (не сходится к 70/120, ошибка −$9/+$6). Это и есть причина, по которой
  ADR ввёл отдельные baseline'ы.
- **Единый per-asset Kilian вместо $12** — для сходимости bear&bull от base нужен
  ~$17/mbpd, вне коридора $10–15 и противоречит зафиксированному $12.
- **Своя mbpd-цепочка для каждой нефти** — не сходится для urals/blend (сжатый
  диапазон от расширяющегося дисконта). Производные — аффинно от Brent.
- **LLM считает μ** — стохастика, неаудируемость (см. §«Почему детерминированно»).

## Implementation

- `nefteboros/forecast/scenarios.py`: `KILIAN_USD_PER_MBPD`, `CALM_BASELINE_BRENT`,
  `OIL_ASSETS`, `DRIVERS` (per-state Δmbpd, каждое с `# source`),
  `DRIVER_BASE_STATES`, `FLAG_PRESETS`, `_DERIVED_OIL_AFFINE`,
  `supply_balance_from_flags()`, `compute_mu_from_flags()`, `ou_params_with_flag_mu()`.
- `nefteboros/forecast/api.py`: `forecast()` принимает keyword-only `flag_states`
  (default None); μ-override + guard non-oil → `ForecastRefusal`; flag-диагностика
  в `metadata` (`flag_states`, `flag_supply_balance_mbpd`).
- `tests/test_forecast_flags.py`: регресс сходимости, реакция на hormuz, инвариант
  bear<base<bull (на μ и на 12m forecast), валидация, обратная совместимость.

## Acceptance / DoD

- [x] Таблица `DRIVERS` (state → Δmbpd), числа из ADR-0023 §Q2, каждое с `# source`
- [x] Per-asset (5 нефтей) восстановление μ; Kilian и calm_baseline — именованные
      константы с `# source`
- [x] `forecast()/scenarios` принимают `flag_states`; дефолт = замороженные μ;
      обратная совместимость (keyword-only, default None)
- [x] Регресс сходимости (bear/base точно, bull ≤$0.04) — `test_forecast_flags.py`
- [x] hormuz blocked→reopened ⇒ μ падает, 12m forecast едет вниз
- [x] Инвариант bear<base<bull сохраняется
- [x] AST-parse затронутых .py. Тесты на Python 3.12 (`.venv312`):
  - `pytest tests/test_forecast_flags.py -m "not network"` → **15 passed, 3 deselected**
    (unit нового файла этого PR)
  - со смежным forecast-регрессом
    (`+ tests/test_ou_sigma_anchor.py + tests/test_forecast_reproducibility.py`) →
    **28 passed, 9 deselected** — без регрессий
  - `pytest tests/test_forecast_flags.py -m network` → **3 passed, 15 deselected**
    (forecast() с flag_states end-to-end; spot из локального кеша `use_cache=True` —
    Yahoo на live отдаёт 429, кеш делает прогон детерминированным и независимым от
    rate-limit)

## Ссылки

- ADR-0023 §Q2 — каталог драйверов, mbpd/\$ калибровка, коридор Kilian $10–15
- ADR-0024 §«Mapping flags → (μ,θ,σ)» — Kilian-цепочка, OU-параметры, противоречие
  baseline $89/$104, таблица флагов (bull hormuz −5) vs проза (−1.7)
- Kilian, L. (2009) "Not All Oil Price Shocks Are Alike" — эластичность ~$10–15/bbl
  per 1 mbpd
- `nefteboros/forecast/scenarios.py`, `nefteboros/forecast/api.py`,
  `tests/test_forecast_flags.py`
