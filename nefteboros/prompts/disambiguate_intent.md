Классифицируй запрос пользователя про нефтегазовый рынок в один из четырёх типов intent.

## Типы intent

- **`forecast_simple`** — прогноз цены **одного** актива из списка валидных (brent, wti, ttf, henry_hub, moexog, gazp, nvtk).
- **`forecast_with_context`** — прогноз нефти в контексте российского бюджета / Минфина / нефтегаздоходов / НДПИ / налоговой формулы. Возвращай `assets=["brent", "urals", "urals_minfin_blend"]` (топ-3 для РФ-аналитика).
- **`russian_gas_refusal`** — запрос про **прямые** цены внутреннего российского газа в рублях / тыс.м³. Прямых daily-котировок нет, нужно direct-redirect к TTF/GAZP/RAG.
- **`out_of_scope`** — вне нефтегазовой темы или вне области расчётного forecast'а.

## Правила приоритизации (ADR-0013 §Constraints)

1. **«нефть/oil/crude/чёрное золото/баррель»** без РФ-контекста → `forecast_simple` с `brent`.
   С РФ-контекстом (Минфин, бюджет, НДПИ, налоги, нефтегаздоходы, российск\*, для казны, для бюджета РФ) → `forecast_with_context`.

2. **Нестандартные/неизвестные марки нефти** (Bonny Light, Sokol, Sahalin Light, Maya, Dubai, Tapis, Brass, Forcados):
   подбери ближайший proxy из списка валидных по физической схожести (light sweet → `brent`; medium sour → `urals`; ESPO/far-east → `urals` или `urals_minfin_blend`) и **обязательно укажи в `refuse_reason`** в формате: «`<asset>` используется как proxy для `<запрошенный>`».

3. **Horizon**: 1m / 3m / 6m / 12m. Меньше 1m или 1d/1w — выставляй `horizon=null` (правило #3 ADR-0013, классификация продолжается без horizon). 18m+ → `out_of_scope` с пояснением про сценарии RAG.

4. **Газ**:
   - «европейский газ / TTF / газ ЕС» → `forecast_simple` с `ttf`.
   - «американский газ / Henry Hub / газ США» → `forecast_simple` с `henry_hub`.
   - «газ» без контекста → `forecast_simple` с `["henry_hub", "ttf"]` (US + EU benchmarks).
   - **«российский газ в рублях / тыс.м³ / для бытового потребителя в РФ»** → `russian_gas_refusal`.

5. Если ничего из вышеперечисленного — `out_of_scope`. Кратко объясни в `refuse_reason` почему.

## Список валидных активов

{ASSET_LIST}

## Examples

Q: «прогноз чёрного золота для российского ТЭК на квартал»
A: `{"type": "forecast_with_context", "assets": ["brent", "urals", "urals_minfin_blend"], "horizon": "3m", "refuse_reason": null}`

Q: «Bonny Light на 6 месяцев»
A: `{"type": "forecast_simple", "assets": ["brent"], "horizon": "6m", "refuse_reason": "brent используется как proxy для Bonny Light (light sweet, аналогичная физика)"}`

Q: «сколько газ стоит для бытовых потребителей в России»
A: `{"type": "russian_gas_refusal", "assets": [], "horizon": null, "refuse_reason": "Прямых daily-котировок внутреннего РФ-газа нет; см. TTF/GAZP/RAG"}`

Q: «погода в Москве»
A: `{"type": "out_of_scope", "assets": [], "horizon": null, "refuse_reason": "Запрос вне нефтегазовой темы"}`

Q: «насколько энергоносители принесут в казну в 2026 году»
A: `{"type": "forecast_with_context", "assets": ["brent", "urals", "urals_minfin_blend"], "horizon": "12m", "refuse_reason": null}`

## Запрос

{QUERY}

## Ответ

Строго один JSON объект соответствующей schema, без markdown-обёртки, без объяснений вне JSON, без префикса/суффикса. Только сам JSON.
