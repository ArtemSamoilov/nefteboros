# Changelog: feature/intent-eval — golden dataset + метрики качества для intent classifier

- **Дата:** 2026-05-07
- **PR:** `feature/intent-eval`
- **Эксперимент:** [docs/experiments/intent_classifier.md](../experiments/intent_classifier.md)

## Задача

Артём (assignee аналитика-ассистента для Сбера): «Метрики качества нам нужны, без них непонятно, работает ли оно все вообще». Собрать golden-датасет на 100 запросов и измерить качество rule-based и hybrid (rule-based + GigaChat-2-Max LLM fallback) intent classifier'а.

## Контекст

PR #8 (`feature/langgraph-subgraph`) и PR #9 (`feature/llm-disambiguate`) добавили rule-based и LLM-узлы в граф. Покрытие unit-тестами — 68/68 passed. Но юнит-тесты — только проверка кода под фиксированным IO; реальную **точность** classification на разнообразии формулировок без гольден-датасета не измерить.

В этом PR:
- Сборка golden-датасета на 100 запросов в 11 категориях.
- Eval-script с метриками: type_accuracy, assets_jaccard, horizon_match, per-class F1, confusion matrix, per-category accuracy.
- Прогон baseline (rule-based-only) и hybrid (rule-based + GigaChat-2-Max).
- По ходу эксперимент вытащил 2 класса ошибок — исправил в этом же PR.

## Что сделано

### Golden-датасет

`datasets/intent_classifier.jsonl` — 100 записей, 11 категорий:
- `A_oil_default` (8) — «нефть» без РФ → brent
- `B_oil_ru_context` (12) — Минфин/бюджет/НДПИ → brent+urals+blend
- `C_wti / D_ttf / E_henry_hub / G_brent` (по 5) — explicit benchmarks
- `F_gas_default` (5) — «газ» → henry_hub+ttf
- `H_russian_gas_refusal` (8) — rule #5
- `I_horizon_refusal` (6) — rule #3 (1d/24m)
- **`J_no_keyword_match_llm` (25)** — главный focus: «чёрное золото», Bonny Light, Maya, Tapis, Sokol, WCS, Forcados, ESPO, СПГ Япония, нефтегаздоходы, баррель — формулировки вне keyword-набора
- `K_out_of_scope` (16) — «погода», «биткоин», «акции», «курс рубля»

Каждая запись: `query / expected_type / expected_assets / expected_horizon / category`.

### Eval-script

`scripts/eval/eval_intent_classifier.py`:
- `--llm/--no-llm` — режим (hybrid / baseline).
- Метрики: type_accuracy, assets_jaccard_mean, horizon_match_rate, per-class precision/recall/F1, per-category accuracy, confusion matrix, per_query trace для разбора failures.
- Output: `metrics/runs/<date>_intent_<rules|llm>_<sha>.json` + краткий summary в stdout.
- `python-dotenv` грузит ближайший `.env` (для `GIGACHAT_*` env при `--llm`).
- `--limit N` — для smoke на первых N примерах.

### Прогоны

Все на sha `8d8800a` (текущий HEAD после фиксов в этом PR):

| Метрика                  | Baseline (rules-only) | **Hybrid (rules + GigaChat-2-Max)** | Δ |
|---|---|---|---|
| `type_accuracy`          | 0.78 | **0.98** | **+0.20** |
| `assets_jaccard_mean`    | 0.64 | **0.79** | +0.15 |
| `horizon_match_rate`     | 0.64 | **0.74** | +0.10 |
| F1 forecast_simple        | 0.77 | **0.98** | +0.21 |
| F1 forecast_with_context  | 0.90 | **0.97** | +0.07 |
| F1 russian_gas_refusal    | 1.00 | **1.00** | — |
| F1 out_of_scope           | 0.67 | **0.98** | +0.31 |

**Главное:** category J (LLM-zone) — **16% baseline → 92% hybrid**. Это и есть deficit, ради которого добавлялся LLM-узел в PR #9.

### Фиксы по ходу эксперимента

В первых прогонах вытащились 2 класса ошибок, исправлены в этом же PR (commit `8d8800a`):

**1. Rule-based: «газ в РФ/для бытовых/для населения» — false positive**

`H_russian_gas_refusal` accuracy была 75% (2 промаха). Запросы «газ в РФ для бытовых потребителей» / «цена газа для населения России» ловились `rule_1_gas_default` (forecast_simple) — до llm_disambiguate не доходило.

Фикс в `nefteboros/graphs/intents.py`:
- Pattern 4 расширен на «росси|рф»: `\bгаз\w{0,3}\s+в\s+(?:росси|рф)\w*\b`.
- Добавлен pattern 7 для «газ для (населен|бытов|потреб)».

После фикса: H = 100% и в baseline, и в hybrid.

**2. LLM возвращает type, но `assets=[]`**

GigaChat-2-Max через `with_structured_output` правильно ставил `type=forecast_simple` на 16 из 25 J-кейсов (Bonny Light, Maya, Tapis, Forcados, Sokol, WCS, ESPO, Murban...), но возвращал **пустой `assets=[]`**. assets_jaccard_mean был 0.68.

Фиксы:
- `nefteboros/prompts/disambiguate_intent.md`: §«Жёсткие требования к JSON» — явные правила по полям (`forecast_simple → assets обязательно ≥1 актив`; `forecast_with_context → ровно [brent, urals, urals_minfin_blend]`; `horizon строго одно из 1m/3m/6m/12m/null`).
- `nefteboros/graphs/nodes/llm_disambiguate.py::_to_intent`: post-process safeguard. Forecast-* type с пустым assets → fallback на `_DEFAULT_ASSETS_BY_TYPE`. Refusal-типы с непустым assets → обнуляем.

После фикса: assets_jaccard_mean 0.68 → 0.79.

### Документ

`docs/experiments/intent_classifier.md` — полное описание дизайна датасета, метрик, результатов, фиксов и known issues.

## Что НЕ в этом PR (явно)

- **Расширение датасета >100 запросов** — текущий золотой набор покрывает базовые сценарии. Расширение под real telemetry (после deploy и сборки реальных вопросов аналитиков) — отдельный PR.
- **`feature/intent-prompt-tuning`** — итеративная подгонка системного промпта `disambiguate_intent.md` на новых cases. Сейчас 2 ошибки на 100 (JKM Asian LNG → out_of_scope, Юралс → forecast_simple) — остались как известные edge cases.
- **Адаптивная regex-эволюция** — не добавляем automatic learning rule-based patterns из LLM-output'ов; делается руками в отдельных PR'ах.
- **Real-LLM smoke на сервере Timeweb** — наш локальный run уже использовал реальный GigaChat (в .env есть credentials). Для production smoke на сервере будем гонять тот же script.
- **Batched / async eval** — текущая реализация sequential (40 LLM-вызовов ~30 секунд). Параллелизация — отдельный PR при необходимости.
- **GitHub Actions для регулярного eval** — будущая инфраструктура; сейчас manual.

## Тесты

- AST OK на новых/изменённых .py.
- pytest 68/68 passed (53 intent_classifier + 8 graph_smoke + 7 llm_disambiguate).
- Real GigaChat-2-Max использован при `--llm` прогоне eval. Cost ≈ 50-80 руб за один полный прогон 100-датасета (~40 LLM-вызовов: J + K + H категории идут в LLM, остальные ловятся rule-based fast path).

## Файлы

**Добавлено (5 файлов):**
- `datasets/intent_classifier.jsonl` — golden dataset, 100 запросов
- `scripts/eval/eval_intent_classifier.py` — eval с метриками
- `docs/experiments/intent_classifier.md` — отчёт
- `docs/changelog/2026-05-07-intent-eval.md` (этот файл)
- `metrics/runs/2026-05-06_intent_rules_8d8800a.json` — baseline run artifact
- `metrics/runs/2026-05-06_intent_llm_8d8800a.json` — hybrid run artifact

**Изменено (3 файла):**
- `nefteboros/graphs/intents.py` — pattern 4 (РФ alias) + pattern 7 (газ для бытовых)
- `nefteboros/graphs/nodes/llm_disambiguate.py` — _to_intent post-process safeguards
- `nefteboros/prompts/disambiguate_intent.md` — жёсткие требования к JSON

**Удалено:** —

## Связанные документы

- ADR-0014: [docs/adr/0014-langgraph-subgraph.md](../adr/0014-langgraph-subgraph.md) — minimal-graph baseline
- ADR-0015: [docs/adr/0015-llm-disambiguate.md](../adr/0015-llm-disambiguate.md) — hybrid disambiguation
- ADR-0013 §«Constraints for SKILL.md» — 5 правил, метрики per category отражают coverage
- Эксперимент: [docs/experiments/intent_classifier.md](../experiments/intent_classifier.md)
- Raw metrics: `metrics/runs/2026-05-06_intent_*_8d8800a.json`
- Предыдущие PR: #8 (`feature/langgraph-subgraph`), #9 (`feature/llm-disambiguate`)
