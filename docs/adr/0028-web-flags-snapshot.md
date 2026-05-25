# ADR-0028 — Новости → состояния флагов → μ: веб-детекция, approve-gate, версионируемый snapshot

- **Дата:** 2026-05-25
- **Статус:** Принято
- **Контекст:** ADR-0025 (этап 1) сделал μ прогноза **детерминированной функцией
  состояний геополитических флагов** (`compute_mu_from_flags`), но явно отложил в
  этап 2 две вещи: (a) **классификацию состояний из новостей** (§Non-goals: «НЕ
  веб / НЕ классификация флагов из новостей — этап 2, отдельный воркер») и (b)
  **непрерывную flag→μ поверхность** (§Known limitations #1: «Непрерывная flag→μ
  поверхность — backlog (вместе с этапом 2)»). Этот ADR — этап 2: новости
  автоматически (с подтверждением) влияют на прогноз цены.
- **Связано:** ADR-0025 (цепочка флаги→μ — **частично superseded**, см. ниже),
  ADR-0024 (OU per scenario), ADR-0022 (Brave web-search), ADR-0015 (LLM-классификатор —
  идиома повторно использована).

## Проблема

Этап 1 принимал состояния флагов как **готовый вход**. Чтобы «новости двигали
прогноз», нужен слой, который:

1. **детектит** состояние каждого драйвера из веба — но защищённо (жёлтая пресса,
   единичный вброс, конфликт источников, старая новость не должны двигать μ);
2. **версионирует** калибровку (что было, что стало, по каким источникам) —
   воспроизводимо и аудируемо;
3. **не применяет молча** — μ прогноза цены для аналитика недопустимо подменять
   по непроверенной новости без человека;
4. **корректно считает μ для ПРОИЗВОЛЬНЫХ веб-комбинаций**, а не только трёх
   пресетов. Здесь вскрываются 3 артефакта калибровки этапа 1 (см. §Артефакты).

## Решение (обзор)

**Полу-авто с approve-gate.** Поток:

```
tier-1 веб → LLM классифицирует состояние драйвера в ЗАКРЫТЫЙ enum (+цитата,
дата, уверенность) → правило ≥2 источников (в КОДЕ) → предложение (diff μ +
источники + guardrails) → ЯВНОЕ подтверждение → версионируемый snapshot → forecast
```

- **Принцип разделения** (как в этапе 1): LLM судит **ФАКТ** («Reuters пишет, что
  Ормуз частично открыт»), число μ считает **детерминированная формула**, а
  **решение менять состояние** — детерминированный код (правило ≥2 источников).
- **μ НЕ хранится** в snapshot — выводится `compute_mu_from_flags(asset, flag_states)`,
  поэтому предложение и прогноз всегда считают **одно и то же** число.

## Детекция: ЗАКРЫТЫЙ enum + правило ≥2 tier-1 источников

`nefteboros/forecast/web_flags/detect.py`.

- **Закрытый enum.** Состояния выводятся НАПРЯМУЮ из `scenarios.DRIVERS` (единый
  источник истины этапа 1) — классификатор не может вернуть состояние, которое не
  понимает `compute_mu_from_flags`. `hormuz`: blocked/partial_reopen/full_reopen/
  partial_closure/full_closure; `iran`: maximum_pressure_active/partial_lift/
  full_lift/further_tightening; `opec_plus`: gradual/accelerated/extended;
  `russia_cap`: active/tightened_dynamic/removed; `china_demand`: base/weak/strong.
- **Tier-1 only.** Поиск через `WebSearcher.search(..., tier_filter="tier1")`
  (whitelist Reuters/Bloomberg/FT/Argus/Platts/… + регуляторы + RU-деловые;
  blacklist соцсетей/агрегаторов/жёлтой прессы — ADR-0022).
- **Смена состояния ТОЛЬКО при ≥2 РАЗЛИЧНЫХ tier-1 хостах** за одно новое
  состояние (`MIN_TIER1_SOURCES=2`). Правило применяется **в коде** (детерминированно,
  аудируемо), а не LLM. Два результата с одного хоста = один источник.
- **Конфликт** (≥2 разных не-prior состояния, каждое по ≥2 источника) → `disputed`,
  состояние **не меняется** (ловушка ТЗ «конфликт источников → не менять / спорно»).
- **Старая новость ≠ изменение.** `event_date` старше `max_event_age_days` (45)
  отбрасывается; непарсящаяся дата НЕ считается устаревшей (lenient). Плюс
  `freshness="pw"` биас на свежесть. (**Known-limitation**, найдено живым прогоном:
  фильтр ловит только СТАРЫЕ даты, не неправдоподобно-БУДУЩИЕ — галлюцинация LLM,
  напр. `2026-06-07`. На результат не влияет при правиле ≥2, но backlog: отсекать
  `event_date > as_of + ε`. См. `docs/experiments/web-flags-live-detect.md`.)
- **Иначе остаётся prior** (предыдущее состояние из активного snapshot, НЕ base).

## Артефакты калибровки этапа 1 — как разрешены (ядро корректности)

Все три — **одна геометрия**. В координатах (Σ Δmbpd, μ_brent) три пресета этапа 1
**не коллинеарны**: bear (+1.6, $70) и bull (−2.57, $120) лежат на прямой с наклоном
−$12/mbpd и пересечением **(0, $89.2)**; а base — это **(0, $98)**. База на **$8.8
выше** линии bear–bull.

- **Артефакт 1 — `partial_closure = −3.27`.** Это число этап 1 подгонял, чтобы bull
  сел на прямую (между −2 прозы и −5 таблицы ADR-0024). Контаминировано.
- **Артефакт 2 — base-anchoring по `balance==0`.** Любая нулевая комбинация (не
  только base-пресет) ошибочно возвращала μ_base=$98.
- **Артефакт 3 — разрыв на стыке base.** non-zero balance шёл по линии $89.2−12·Σ,
  а `balance==0` спец-кейсился на $98 → скачок $8.8 ровно в текущей точке (где живут
  новости). Хуже: малый ДЕФИЦИТ (например iran further_tightening, −0.2 mbpd) ронял
  μ к $91.6 — **инверсия направления** (дефицит должен поднимать цену).

### Выбор: НЕПРЕРЫВНАЯ поверхность, anchored на наблюдаемой base μ

Реализована та самая «непрерывная flag→μ поверхность», которую ADR-0025
§Known-limitations#1 отложил в этап 2. Якорим на **единственной реальной точке** —
base μ (≈ spot $100), а не на фиктивном $89.2 (сам ADR-0025 называет его «фитируемый
якорь, НЕ независимая цена спокойного рынка»). Поверхность кусочно-линейна и
**непрерывна в base**, per-asset:

```
balance ≥ 0 (де-эскалация): μ = μ_base − (μ_base − μ_bear)/bear_balance · balance
balance < 0 (эскалация):    μ = μ_base − (μ_bull − μ_base)/(−bull_balance) · balance
```

где μ_bear/μ_base/μ_bull — замороженные μ пресетов из `ASSET_PARAMS[asset]`,
bear_balance=+1.6, bull_balance=−1.3.

**Это закрывает все три артефакта одним ходом:**

1. **`partial_closure` вернулся к честной −2.0** (проза ADR-0024 «supply tightening
   ≈ −2» + `FLAGS_DECOMPOSITION` bull «−2 mbpd»). bull-баланс стал **−1.3** — ровно
   как в summary самого этапа 1 («deficit −1.3 mbpd»). Затычка $15 ушла из физической
   дельты драйвера в **эффективную эластичность** (где ей место — см. ниже).
2. **Нет спец-кейса `balance==0`.** base — это естественное значение поверхности в
   нуле (μ_base − slope·0), а не отдельная ветка. Произвольная нулевая комбинация
   даёт ≈μ_base непрерывно.
3. **Нет разрыва.** Поверхность непрерывна в base; малое отклонение даёт малый сдвиг
   μ в ВЕРНОМ направлении (china weak +0.4 → $91 вместо артефактных $84.4; iran
   further_tightening −0.2 → $101.4 вместо инверсных $91.6).

Свойства: **непрерывно, монотонно** (инвариант bear<base<bull держится), **три
пресета воспроизводятся ТОЧНО** (max |diff| = 0.000 против ≤$0.04 этапа 1, т.к.
каждая нефть интерполирует СВОИ замороженные μ — дифференциал urals/espo «дисконт
ширится с ценой» выходит автоматически, без отдельной аффинной карты этапа 1).

### Артефакт 4 — экстраполяция пробивает structural floor/ceiling (structural clamp)

Найдено при независимой проверке калибровки (ревью). Линейная поверхность за
пределами пресетов экстраполируется без structural awareness и пробивает физические
границы нефти:

- `full_reopen + full_lift + accelerated + removed + weak` (+5.6 mbpd) → brent **$24.5**
  (ниже cost-of-production floor $40-50, ADR-0024 §«structural floor/ceiling»);
- `full_closure + further_tightening + strong` (−5.6 mbpd) → brent **$183** (выше
  demand-destruction ceiling $120-150).

Δμ-cap (35%) и approve-gate гейтят **ПРИМЕНЕНИЕ** обновления, но `compute_mu_from_flags`
зовётся и **в обход** gate (прямой вызов, `ou_params_with_flag_mu`) — значит защита
обязана жить **в самой μ-функции**. Решение: жёсткий structural clamp
`μ ∈ [OIL_MU_FLOOR=$40, OIL_MU_CEILING=$155]` (ADR-0024 floor/ceiling; rail взят чуть
шире soft-коридора — floor по нижней границе $40, ceiling $155 на ~$5 выше $150 —
чтобы клэмпить ТОЛЬКО структурно невозможное, не задевая пресеты: brent bull $120
внутри). Глобальный для всех нефтей (консервативно; per-asset bounds — backlog).
**15/15 пресетов внутри коридора ⇒ backward-compat не затронут** (PR #77 не двинут).
Тест — `tests/test_forecast_flags.py::TestStructuralClamp`.

### Цена выбора и её защита (саморазгром)

Эффективная эластичность brent стала **~$17/mbpd** (де-эск 17.5, эск 16.9) вместо
$12. ADR-0025 явно отверг $17 как «вне коридора Kilian $10–15». Аргумент за $17:

- $12 у этапа 1 работает **только** на фиктивном якоре $89.2; при якоре на **реальном**
  $98 те же банк-консенсус-таргеты ($70/$120) **математически дают ~$17** — это не
  новое число, а следствие честного якоря.
- Для **новостной** реакции $17 честнее: новость двигает не только supply-баланс,
  но и **риск-премию** (≈ $12 supply Kilian + ~$5 репрайс премии в shock-режиме).
  Goldman $115 vs $90 при +2 mbpd ($12.5) — это *long-run persistent*; спот на
  заголовки реагирует резче.
- `KILIAN_USD_PER_MBPD=$12` сохранён как **литературный референс** (Kilian 2009,
  кросс-ссылки ADR-0024/0025), но больше **не множитель формулы**.

**Оставшиеся слабые места (размечены, не закрыты):**

1. Лёгкий кинк в base (17.5 vs 16.9 mbpd) = асимметрия эластичности; численно
   $0.6/mbpd — пренебрежимо.
2. **Экстраполяция за пределы [bull, bear] = [−1.3, +1.6] mbpd** линейна и без
   structural awareness — **закрыто structural clamp** (см. §Артефакт 4). Внутри
   `[floor, ceiling]` вне пресетов поверхность даёт только **направление** (точность
   не заявляется), на границах — жёсткий клэмп $40/$155 + Δμ-cap + дисклеймер.
3. θ/σ под флаги по-прежнему не калибруются (наследие non-goal ADR-0025).

### Supersede ADR-0025 (явно)

- §Known limitations **#1** (разрыв на стыке base) — **закрыт** непрерывной поверхностью.
- §Known limitations **#2** (bull-флаги −2.57) — **отменён**: bull = −1.3 (честная физика).
- §«base — особый случай, anchored… Σ=0 ⇒ возврат μ_base» — **superseded**: base
  теперь непрерывная точка поверхности, а не спец-кейс (численно тот же $98).
- `CALM_BASELINE_BRENT` и аффинная карта `_DERIVED_OIL_AFFINE` — **удалены** (не нужны).
- Тесты `test_bear_headline_chain_exact` / `test_base_is_anchored_not_calm_baseline` /
  `test_bull_balance_matches_reconciliation` этапа 1 переписаны под новую поверхность.

## Approve-gate: почему не молча

`nefteboros/forecast/web_flags/propose.py`. Единственный путь, меняющий активный
snapshot — `apply_proposal(..., confirm=True)`.

- `build_proposal` **чист** (ничего не пишет) — детекция и расчёт diff не имеют
  побочных эффектов.
- `apply_proposal` без `confirm=True` поднимает `ApprovalRequired` и логирует `reject`.
- **Почему не молча:** μ — детерминированный вход прогноза цены нефти для аналитика
  Г. Грефа. Автоподмена калибровки по непроверенной новости = риск молча сдвинуть
  прогноз на десятки долларов. Человек видит **diff** (μ старое→новое по всем нефтям),
  **причину** (переход состояния), **цитаты tier-1 с датами** и **вердикт guardrails** —
  и решает. Approve — через CLI (`detect --apply [--yes]`), UI или explicit flag.

## Guardrails

`nefteboros/forecast/web_flags/guardrails.py`. Три проверки на каждый переход:

1. **Δμ-cap** — |Δμ|/μ_old по каждой нефти ≤ `cap_pct` (default **35%**: одиночный
   Hormuz reopen ≈ 27% проходит, >35% за апдейт = несколько крупных событий разом →
   требует override). Это **ГЕЙТ, не клэмп**: μ не хранится (выводится из flag_states),
   её нельзя «подрезать» — слишком большой скачок **блокирует** авто-применение
   (нужен `force=True` при approve), а не молча обрезается.
2. **Инвариант bear<base<bull** — монотонность μ-поверхности (регресс-страж калибровки).
3. **Направление** — Δ supply-баланса и Δμ_brent противоположных знаков (профицит ⇒
   ниже μ); ловит инверсию знака.

**Полный diff-лог** — `changelog.jsonl` в хранилище snapshot: каждое событие
(seed/propose/reject/blocked/apply/activate) с timestamp, версией, переходами,
дельтами, нарушениями guardrails.

## Reproducibility через snapshot

- `forecast()` читает flag_states **активного** snapshot (`active_forecast`), который
  меняется **только** через approved-апдейт. Между апдейтами прогноз **детерминирован**.
- **Веб дёргается только при обновлении**, не на каждый forecast. TTL активного
  snapshot (`should_refresh`, default 24ч) подсказывает, когда перепроверять новости;
  Brave-ответы кэшируются 1ч (ADR-0022).
- **Дефолт `forecast()` (flag_states=None) НЕ изменён** — обратная совместимость
  этапа 1 сохранена. seed-snapshot = состояние 2026-05-08 = base-пресет ⇒ seed-прогноз
  совпадает с дефолтным `forecast()`.

## Non-goals (вне scope)

- **Молчаливое авто без approve** — отвергнуто (см. §Approve-gate).
- **Авто-расписание детекции** — backlog (TTL-хук есть, cron — отдельно).
- **Газ / equity** — `flag_states` для них → `ForecastRefusal` (наследие ADR-0025).
- **θ/σ под флаги** — по-прежнему из scenario-пресета.

## Что отвергли

- **Дисклеймер-only (оставить цепочку этапа 1 нетронутой, «вне 3 пресетов
  ориентировочно»)** — это сохраняет разрыв $9 ровно там, где живут новости, и
  контаминированный partial_closure. Для demo «новости двигают прогноз» — слабо.
- **Вторая μ-функция для веб-комбинаций** — создаёт dual-μ: предложение показывает
  одно число, а `forecast()` (через `ou_params_with_flag_mu`) считает другое.
  Approve-gate обязан показывать **то же** число, что даст прогноз ⇒ μ-функция одна.
- **Якорь на calm $89.2 + чистый $12 (этап 1)** — фиктивный якорь, разрыв в base.
- **Клэмп μ к cap** — μ не хранится; клэмп рассинхронит snapshot и `compute_mu_from_flags`.
- **LLM считает μ** — стохастика, неаудируемость (наследие решения ADR-0025).

## Implementation

- `nefteboros/forecast/web_flags/`: `models.py` (FlagSource, DriverDetection,
  CalibrationSnapshot, AssetMuDelta, GuardrailReport, MuProposal; enum из DRIVERS),
  `detect.py` (DriverStateClassifier + FlagDetector + правило ≥2), `snapshot.py`
  (SnapshotStore: версии + ACTIVE + changelog.jsonl + TTL), `guardrails.py`,
  `propose.py` (build/propose/apply approve-gate + active_forecast).
- `nefteboros/forecast/scenarios.py`: непрерывная `compute_mu_from_flags` +
  structural clamp (`OIL_MU_FLOOR=$40`, `OIL_MU_CEILING=$155`); `partial_closure
  −3.27→−2.0`; удалены `CALM_BASELINE_BRENT`, `_DERIVED_OIL_AFFINE`;
  `KILIAN_USD_PER_MBPD` репозиционирован как референс.
- `scripts/forecast_web_flags.py`: CLI (status / detect [--apply --yes --force] /
  forecast / log).
- `tests/test_web_flags.py` (mock-web, без сети/LLM); `tests/test_forecast_flags.py`
  (3 теста переписаны + непрерывность).

## Acceptance / DoD

- [x] Детекция: tier-1 веб → закрытый enum (= ключи DRIVERS) + цитата + дата +
      уверенность; смена ТОЛЬКО при ≥2 tier-1; конфликт → не менять; старое ≠ изменение.
- [x] CalibrationSnapshot(as_of, flag_states, sources); μ НЕ хранится (выводится).
- [x] Approve-gate: предложение (diff + источники) → применяется ТОЛЬКО по confirm.
- [x] Guardrails: Δμ-cap (гейт), инвариант bear<base<bull, направление, полный diff-лог.
- [x] Reproducibility: forecast детерминирован в пределах snapshot; веб — только на апдейт.
- [x] Артефакты 1-3 разрешены непрерывной base-anchored поверхностью; артефакт 4
      (экстраполяция за floor/ceiling) — structural clamp; supersede 0025.
- [x] Тесты mock-web (Python 3.12, `.venv312`):
  - `pytest tests/test_web_flags.py` → **34 passed** (детекция/≥2-source/конфликт/
    устаревание, версионирование, approve-gate, guardrails, reproducibility, wiring)
  - `pytest tests/test_forecast_flags.py` → **18 passed** (3 теста под новую поверхность
    + `TestStructuralClamp`: экстремальный surplus→floor, deficit→ceiling, пресеты не двинуты)
  - со смежным forecast-регрессом (`+ test_ou_sigma_anchor + test_forecast_reproducibility`)
    → **65 passed** суммарно — без регрессий
  - `pytest tests/test_web_flags.py -m network` → **1 passed** (реальный kimi-k2p6:
    строго закрытый enum, извлечение event_date, distractor→none — классификатор
    подтверждён на ЖИВОМ LLM, не только на mock)
  - AST-parse всех затронутых .py — ok
- [x] **Живой Brave-fetch прогнан** (follow-up, 2026-05-25, commit 8aa4e0d): реальный
      Brave tier-1 + kimi-k2p6 → `china_demand: base→weak` (2 tier-1: Reuters + OilPrice,
      conf 0.88) ⇒ μ brent $98→$91, guardrails OK; hormuz/opec по 1 источнику → НЕ менялись
      (≥2-правило). Tier-1 фильтр, классификатор на живой выдаче, ≥2-правило и continuous
      surface — подтверждены вживую. Детали — `docs/experiments/web-flags-live-detect.md`.
      Предложение НЕ применено (advisory; apply — отдельное approve-решение ПОСЛЕ demo, с
      пересчётом forecast-документа под новую базу).

## Ссылки

- ADR-0025 — детерминированная цепочка флаги→μ (частично superseded)
- ADR-0024 — OU per scenario, Kilian-цепочка, противоречие baseline $89/$104
- ADR-0022 — Brave web-search, tier-1 whitelist
- ADR-0015 — LLM-классификатор (идиома)
- Kilian, L. (2009) "Not All Oil Price Shocks Are Alike"
- `nefteboros/forecast/web_flags/`, `nefteboros/forecast/scenarios.py`,
  `scripts/forecast_web_flags.py`, `tests/test_web_flags.py`
