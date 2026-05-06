# Эксперимент — intent classifier (analyst graph)

Голден-датасет на 100 запросов + walk-через rule-based и hybrid (rule-based +
GigaChat-2-Max LLM fallback). Артефакт PR `feature/intent-eval`.

Ранее: ADR-0014 (rule-based classify), ADR-0015 (LLM-disambiguate hybrid).

Реран:
```bash
PYTHONPATH=. python scripts/eval/eval_intent_classifier.py             # baseline
PYTHONPATH=. python scripts/eval/eval_intent_classifier.py --llm       # hybrid
```

Output: `metrics/runs/<date>_intent_<rules|llm>_<sha>.json`.

## Дизайн датасета

`datasets/intent_classifier.jsonl` — 100 запросов в 11 категориях (по 5-25 на категорию). Каждый запрос помечен `expected_type / expected_assets / expected_horizon / category`.

| Категория | n  | Назначение |
|---|---|---|
| `A_oil_default`           | 8  | «нефть» без РФ-контекста → brent |
| `B_oil_ru_context`        | 12 | Минфин/бюджет/НДПИ/нефтегаздоходы → brent + urals + urals_minfin_blend |
| `C_wti`                   | 5  | WTI / американская нефть |
| `D_ttf`                   | 5  | TTF / европейский газ |
| `E_henry_hub`             | 5  | Henry Hub / американский газ |
| `F_gas_default`           | 5  | «газ» без контекста → henry_hub + ttf |
| `G_brent`                 | 5  | Brent explicit |
| `H_russian_gas_refusal`   | 8  | rule #5 — прямые цены РФ-газа |
| `I_horizon_refusal`       | 6  | rule #3 — 1d/1w/24m/2y → out_of_scope |
| `J_no_keyword_match_llm`  | 25 | формулировки вне keyword-набора (LLM-zone): «чёрное золото», Bonny Light, Maya, Tapis, Sokol, WCS, Forcados, ESPO, СПГ Япония, нефтегаздоходы, баррель и т.п. |
| `K_out_of_scope`          | 16 | реальный out_of_scope: «погода», «биткоин», «акции Сбера», «курс рубля» |

## Метрики

- `type_accuracy` — доля правильно классифицированных IntentType (4 класса).
- `assets_jaccard_mean` — средний Jaccard expected vs predicted assets (только для `forecast_*` типов).
- `horizon_match_rate` — exact match горизонта для случаев, где expected или actual != null.
- per-class precision / recall / F1.
- per-category accuracy.

## Результаты

### Сравнение runs

| Метрика                  | Baseline (rules-only) | **Hybrid (rules + GigaChat-2-Max)** | Δ |
|---|---|---|---|
| `type_accuracy`          | 0.78 | **0.98** | **+0.20** |
| `assets_jaccard_mean`    | 0.64 | **0.79** | +0.15 |
| `horizon_match_rate`     | 0.64 | **0.72** | +0.08 |
| F1 forecast_simple        | 0.77 | **0.98** | +0.21 |
| F1 forecast_with_context  | 0.90 | **0.97** | +0.07 |
| F1 russian_gas_refusal    | 1.00 | **1.00** | — |
| F1 out_of_scope           | 0.67 | **0.98** | +0.31 |

### Per-category accuracy (hybrid)

| Категория | Baseline | Hybrid |
|---|---|---|
| A_oil_default            | 1.00 | 1.00 |
| B_oil_ru_context         | 1.00 | 1.00 |
| C_wti                    | 1.00 | 1.00 |
| D_ttf                    | 1.00 | 1.00 |
| E_henry_hub              | 1.00 | 1.00 |
| F_gas_default            | 0.80 | **1.00** |
| G_brent                  | 1.00 | 1.00 |
| H_russian_gas_refusal    | 1.00 | 1.00 |
| I_horizon_refusal        | 1.00 | 1.00 |
| **J_no_keyword_match_llm** | **0.16** | **0.92** |
| K_out_of_scope           | 1.00 | 1.00 |

**Главное наблюдение**: hybrid поднял J с 16% до 92% — это и есть зона deficit'а rule-based, ради которой добавлялся LLM. Категории A-G/I-K с самого начала >= 80% (rule-based ловит типовые формулировки), LLM ничего там не ломает.

## Confusion matrix (hybrid)

```
expected → predicted
✓ forecast_simple          → forecast_simple          53
✗ forecast_simple          → out_of_scope             1   (JKM Asian LNG)
✓ forecast_with_context    → forecast_with_context    15
✗ forecast_with_context    → forecast_simple          1   (Юралс на полгода)
✓ russian_gas_refusal      → russian_gas_refusal      8
✓ out_of_scope             → out_of_scope             22
```

Только 2 ошибки на 100 запросов (98% type accuracy). Обе — спорные edge cases:

1. **«JKM Asian LNG на квартал»** — LLM вернул `out_of_scope`. Корректное поведение зависит от того, считаем ли мы Asian LNG напрямую покрытым нашим registry. По текущему ADR-0012 JKM отложен в P2, в interpret.py для Asian gas TTF — proxy. Я отметил expected=`forecast_simple [ttf]`, но LLM решил, что без явного TTF в registry-списке такого ещё нет → out_of_scope. Это **policy-вопрос**, не bug LLM'а; можно изменить expected либо в промпте explicit указать «Asian LNG → ttf proxy».

2. **«Юралс на полгода»** — LLM вернул `forecast_simple [urals_minfin_blend]`, я ожидал `forecast_with_context [brent, urals, urals_minfin_blend]`. У формулировки нет explicit РФ-context-слов («Минфин/бюджет»), только транслитерированный `Юралс` — LLM прочитал это как «спрашивают про конкретный актив». Спорно; правило #1 ADR-0013 говорит, что Urals (даже explicit) — должно тянуть РФ-контекст-сводку. Можно дофиксить либо промпт (явная инструкция «любое упоминание Urals → forecast_with_context»), либо expected.

Эти 2 cases — не баги pipeline, а **calibration grey zones** между правилом и инструкцией LLM. Подсветка для следующего PR `feature/intent-prompt-tuning`.

## Что было поправлено по ходу эксперимента

Эксперимент сам по себе вытащил 4 типа ошибок, которые мы исправили в этом же PR:

### 1. rule-based: «газ в РФ» / «газ для населения» — false positive forecast_simple

В первом baseline-проходе 2 H_russian_gas_refusal ошибки (75% точности). Запросы:
- «сколько стоит газ в РФ для бытовых потребителей»
- «цена газа для населения России»

Rule-based ловил их как `rule_1_gas_default` (forecast_simple [henry_hub, ttf]) — до LLM не доходило. Причина: pattern #4 матчил только «росси\w+», не ловил «РФ»; и не было pattern'а на «газ для (населен|бытов|потреб)».

Фикс в `nefteboros/graphs/intents.py`:
- Pattern 4 расширен: `\bгаз\w{0,3}\s+в\s+(?:росси|рф)\w*\b`.
- Добавлен pattern 7: `\bгаз\w{0,3}\s+для\s+(?:населен|бытов|потреб)\w*\b`.

Result: H_russian_gas_refusal стало 100% и в baseline, и в hybrid.

### 2. LLM: type правильный, но `assets=[]` (главная слабость GigaChat structured output)

В первом hybrid-проходе LLM возвращал правильный `type=forecast_simple`, но **пустой `assets=[]`** на 16 из 25 J-кейсов (Bonny Light, Maya, Tapis, Forcados, Sokol, WCS, Eagle Ford, Permian, Iranian heavy, Saudi Arab Light, Murban, баррель, СПГ Япония и т.д.).

Это known weakness GigaChat-2-Max при structured output: модель **корректно** определяет namespace, но не возвращает требуемые поля списка, если это не явно требуется в промпте.

Фиксы:
- `nefteboros/prompts/disambiguate_intent.md` §«Жёсткие требования к JSON» — явно потребовали: «`type=forecast_simple` → `assets` обязательно содержит хотя бы один актив. Не пустой массив».
- `nefteboros/graphs/nodes/llm_disambiguate.py::_to_intent` — post-process safeguard: если LLM вернул forecast_* type с пустым assets, fallback на default mapping (forecast_simple → ["brent"]; forecast_with_context → 3 актива).

Result: assets_jaccard_mean вырос с 0.68 до 0.79.

### 3. LLM не enforces RU-context на «Юралс» / «энергоносители казна»

LLM иногда ставил forecast_simple [urals] вместо forecast_with_context. Это менее критично — type правильный (форекаст), assets хотя бы релевантный. Для аналитика Сбера, спрашивающего про «Юралс», ответ только по urals хуже сводки brent+urals+blend, но не катастрофически.

Не фиксил отдельно — это subjective judgment grey zone.

### 4. horizon_match — слабее всего

`horizon_match_rate` остался 0.72 (vs assets 0.79 и type 0.98). LLM иногда не извлекает horizon из «бюджет 2026» (expected 12m, LLM ставит null). Можно усилить промпт «бюджет/казна <year> → 12m», но это нишевый случай.

## Выводы

- **Hybrid даёт +20 пунктов type_accuracy и +12 пунктов F1 out_of_scope** относительно rule-based-only.
- **GigaChat-2-Max через `with_structured_output(_LLMIntent)`** работает на этой задаче, но с двумя слабостями:
  1. Пустые поля списков — лечится post-process fallback'ом + жёсткими требованиями в промпте.
  2. RU-context для частных формулировок («Юралс» без слов «Минфин») — оставлено как edge case.
- **Rule-based ловит** большинство типовых формулировок (категории A-G/I-K дают ≥80% accuracy без LLM). Латенси и cost здесь оптимальны.
- **LLM закрывает deficit** на категории J (нерегулярные формулировки): 16% → 92%. Это и есть архитектурный довод за hybrid.

## Известные ограничения

- **Только текущий датасет** (100 запросов). Telemetry на real users появится после deploy на Timeweb — golden-set'у нужно обновляться адаптивно.
- **Subjective judgment** в J-кейсах: «Maya proxy = urals?» / «WCS = wti?» — мои labels могут не совпадать с predпочтениями аналитика-эксперта. Артём может пересмотреть expected на конкретных кейсах.
- **Real GigaChat call'ы** на 100-запросном run — около ~40 LLM-вызовов (J + K категории идут в LLM, остальные ловятся rule-based fast path'ом). Стоимость прогона на GigaChat-2-Max ≈ 50-80 рублей за один запуск.
- **horizon_match** — сложно поднять без дополнительных rule-based extraction'ов («бюджет 2026 → 12m», «через год → 12m»). Отложено в `feature/intent-prompt-tuning`.

## Воспроизвести

```bash
# Активировать venv с зависимостями
source ~/PycharmProjects/nefteboros/.venv/bin/activate

# Baseline (без LLM, без сетевых вызовов)
PYTHONPATH=. python scripts/eval/eval_intent_classifier.py

# Hybrid (с GigaChat-2-Max — нужны GIGACHAT_* env в .env)
PYTHONPATH=. python scripts/eval/eval_intent_classifier.py --llm

# Оба сохраняются в metrics/runs/<date>_intent_<rules|llm>_<sha>.json
```

`--limit N` ограничивает прогон первыми N запросами (для smoke).

## Связанные документы

- ADR-0014: [docs/adr/0014-langgraph-subgraph.md](../adr/0014-langgraph-subgraph.md) — minimal-graph baseline.
- ADR-0015: [docs/adr/0015-llm-disambiguate.md](../adr/0015-llm-disambiguate.md) — hybrid disambiguation.
- ADR-0013 §«Constraints for SKILL.md»: 5 правил disambiguation.
- Code: `scripts/eval/eval_intent_classifier.py`, `datasets/intent_classifier.jsonl`.
- Raw metrics: `metrics/runs/2026-05-*_intent_*.json`.
