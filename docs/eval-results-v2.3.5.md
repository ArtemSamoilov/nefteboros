# E2E re-baseline на v2.3.5+

## Шапка прогона

| Параметр | Значение |
|---|---|
| Версия (HEAD на момент прогона) | `c3c22f6` (PR #48 поверх v2.3.5 / `ba783d4`) |
| Тег | `v2.3.5` + один коммит (`fix/auto-enable-web-search`) |
| Runner | `WSRunner` (default, см. PR от 2026-05-10) |
| Dataset | `datasets/e2e_dialogues.jsonl` (n=100) |
| Окружение | local (macOS Darwin 24, M-series) |
| Python | 3.12.12 (`uv venv -p 3.12`, см. memory `reference_python_versions.md`) |
| Server | `python server.py`, `OUROBOROS_SERVER_PORT=8000`, prod-config через `scripts/deploy/apply_production_config.py` |
| RAG vectorstore | `NEFTEBOROS_RAG_VECTORSTORE_PATH` → parent repo, collection `nefteboros_corpus_v2_heading` |
| LLM | PRIMARY=`hydra/kimi-k2p6` (synthesize), ROUTING=`gigachat/GigaChat-2-Max` (classify, disambiguate) |
| Старт | 2026-05-11 20:23:59 MSK |
| Финиш | 2026-05-12 07:00:20 MSK |
| Время прогона | **10 ч 36 мин** |
| Артефакт | `metrics/runs/2026-05-12T04-00-20Z_e2e_ws_c3c22f6.json` |

## Метрики на всех 100 диалогах

Сравнение с baseline v1.0 (из задания координатора):

| Метрика | v1.0 baseline | v2.3.5+ (n=100) | Δ абс. | Δ % | Comment |
|---|---|---|---|---|---|
| `success_rate` | 0.568 | **0.344** | −0.224 | −39.4% | regression (>5pp) |
| `citation_correctness` | 0.181 | **0.362** | +0.181 | +100.0% | improvement |
| `structure_adherence` | 0.528 | **0.489** | −0.039 | −7.4% | в пределах шума |
| `refusal_rate` | 0.947 | **0.368** | −0.579 | −61.1% | regression (>5pp) |

Applicable-counts на v2.3.5+: success=90, citation=47, structure=47, refusal=19. Т.е. цитирование/структура считались на 47 диалогах (non-refusal-expected), refusal — на 19 (refusal-expected).

### Срез dev (n=82) vs held_out (n=18)

| Метрика | DEV (82) | HELD_OUT (18) |
|---|---|---|
| `success_rate` | 0.365 | 0.250 |
| `citation_correctness` | 0.425 | 0.000 |
| `structure_adherence` | 0.525 | 0.286 |
| `refusal_rate` | 0.467 | 0.000 |

Held_out тащит средние вниз (см. § «Регрессии»).

### По сценариям

| Scenario | n | success | cite | struct | refusal |
|---|---|---|---|---|---|
| `forecast` | 16 | 0.600 | 0.700 | 0.800 | — |
| `rag_only` | 16 | 0.500 | 0.545 | 0.545 | — |
| `rag_plus_web` | 2 | 0.500 | 1.000 | 1.000 | — |
| `web_only` | 10 | 0.400 | 0.125 | 0.125 | — |
| `multi_tool` | 16 | 0.250 | 0.143 | 0.571 | — |
| `follow_up` | 8 | 0.250 | 0.000 | 0.667 | — |
| `adversarial` | 10 | 0.250 | 0.000 | 0.000 | 0.500 |
| `out_of_scope` | 14 | 0.000 | — | — | 0.357 |
| `unknown_with_hypothesis` | 8 | 0.125 | 0.250 | 0.250 | — |

`forecast` — единственный сценарий выше baseline по всем трём метрикам. `web_only`, `multi_tool`, `follow_up`, `adversarial`, `unknown_with_hypothesis` — заметная просадка.

## Регрессии (>5pp хуже baseline)

### 1. `success_rate` −22.4pp и `refusal_rate` −57.9pp — главная причина: **43 timeout > 360s**

43 диалога из 100 не уложились в WSRunner timeout (360 s). Per-scenario распределение таймаутов:

| Scenario | timeouts |
|---|---|
| `multi_tool` | 9 |
| `out_of_scope` | 7 |
| `forecast` | 5 |
| `rag_only` | 5 |
| `follow_up` | 5 |
| `adversarial` | 5 |
| `unknown_with_hypothesis` | 4 |
| `web_only` | 2 |
| `rag_plus_web` | 1 |

ID диалогов: `e2e_0012, 0028, 0033, 0052, 0053, 0056, 0057, 0060, 0063, 0064, 0065, 0066, 0067, 0069, 0070, 0073–0100` (диапазон 0073–0100 — почти сплошной хвост, 28 диалогов).

**Главная гипотеза — накапливающаяся деградация server-side**: timeout-ы распределены не случайно, а сосредоточены в хвосте (0073→0100 практически полностью). Это паттерн memory/connection leak или накопления состояния в Ouroboros agent loop / Langfuse JSONL tracer / chromadb client.

**Условные метрики на 57 не-таймаутных диалогах**:

| Метрика | v1.0 baseline | v2.3.5+ (ok-only, n=57) | Δ абс. |
|---|---|---|---|
| `success_rate` | 0.568 | 0.660 | +0.092 |
| `citation_correctness` | 0.181 | 0.362 | +0.181 |
| `structure_adherence` | 0.528 | 0.489 | −0.039 |
| `refusal_rate` | 0.947 | 0.700 | −0.247 |

На «отвечающих» диалогах success/cite уже выше baseline, structure ≈ baseline; refusal остаётся ниже, но n=10 — статистически слабо.

**Это значит главная регрессия — не в качестве LLM-ответа, а в latency / stability пайплайна.**

### 2. `refusal_rate` 0.368 vs 0.947

Дополнительно к таймаутам: из 9 fail-refusal-applicable диалогов `out_of_scope` (`e2e_0044, 0045, 0089–0095`) часть тоже в таймаутах. Не-таймаутные out_of_scope-failures: `e2e_0044, 0045` (`expected_refusal=True`, агент не отказал). Подгруппа `adversarial` — `refusal_rate=0.500` (из 10 диалогов 5 успешно отказались).

Связь с известными багами из памяти (`feedback_d_base_baseline.md`):
- prompt injection в `e2e_0100` — диалог в таймауте, ответ не получен.
- llm_disambiguate None — не подтверждено в этом прогоне (нужен анализ trace).

### 3. `success` на `multi_tool` / `follow_up`

- `multi_tool` n=16, success=0.25, cite=0.143 — соответствует известному багу «RAG/web gap в графе» (память).
- `follow_up` n=8, success=0.25 — соответствует «multi-turn lost» (память).

Эти просадки не новые, они уже были в v1.0 baseline; здесь они дополнительно усугубились таймаутами.

### 4. `adversarial`, `unknown_with_hypothesis` — категории, отсутствующие у v1.0

В v1.0 dataset не было `held_out=True` с `adversarial`/`unknown_with_hypothesis`. Сейчас в выборке 100 диалогов 18 held_out, и они тянут среднее вниз (`success=0.25`, `cite=0.0`, `refusal=0.0`). Это смена выборки, не строго регрессия модели.

## Улучшения

### `citation_correctness` +18.1pp (0.181 → 0.362)

Удвоилась на той же выборке. Возможные источники:
- PR #36 (D6 citations + D5 structure) — расширены regex для RAG/Web/Forecast и нормализация цитат в `nefteboros.citations`.
- PR `auto-enable-skill` — skill `neftegaz_analyst` сразу видна в первом round'е (`OUROBOROS_AUTO_ENABLE_SKILLS=neftegaz_analyst:analyst_query,neftegaz_analyst:rag_search`), не теряется первый запрос.
- v2.3.x — `synthesize` обязательно эмитит цитаты, см. system_prompt-analyst.

По сценариям прирост особенно заметен на `forecast` (0.700) и `rag_plus_web` (1.000); на `rag_only` (0.545) тоже выше baseline.

### `forecast` scenario — единственный «прокачанный» по всем метрикам

`success=0.6, cite=0.7, struct=0.8` (n=16). v1.0 baseline на forecast был ниже (точных цифр per-scenario в задании нет). Возможные источники: PR ADR-0012 (yfinance + SARIMAX exo, без Prophet) и `forecast-skill` v2.3.x.

## Технические замечания

### Время прогона

10 ч 36 мин. По плану «~2 мин/диалог × 100 = ~3.5 ч», по факту 43 диалога ушли в 360 s timeout (= 4.3 ч таймаутов) + sequential 3 s inter-dialogue sleep × 99 ≈ 5 мин. Кроме того, не-таймаутные диалоги в хвосте (e2e_0068, 0071, 0072) шли заметно дольше, чем в первой половине — индирект-сигнал серверной деградации.

### Чекпоинты — incremental save каждые 10 диалогов

Eval-скрипт пропатчен (см. PR diff в [scripts/eval/eval_e2e.py:858](../scripts/eval/eval_e2e.py:858)): после каждых 10 диалогов вызывается `save_run(..., partial_done=N)` с суффиксом `_partial_NNN` в имени файла. Это покрывает риск, который реализовался в первом запуске прогона: процесс умер на 91-м диалоге без сохранения итогового JSON, метрики первой попытки потеряны.

Чекпоинты текущего прогона:

```
metrics/runs/2026-05-11T17-47-43Z_e2e_ws_c3c22f6_partial_010.json
metrics/runs/2026-05-11T18-11-06Z_e2e_ws_c3c22f6_partial_020.json
metrics/runs/2026-05-11T18-44-52Z_e2e_ws_c3c22f6_partial_030.json
…
metrics/runs/2026-05-12T03-39-29Z_e2e_ws_c3c22f6_partial_090.json
metrics/runs/2026-05-12T04-00-20Z_e2e_ws_c3c22f6.json   # final
```

### Valid trace ratio в Langfuse — **N/A в этом прогоне**

`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` в локальной `.env` не настроены — Langfuse Cloud отключён, observability работает в `nefteboros.observability.tracer` JSONL backup. В server log за весь прогон видны warnings:

```
WARNING langfuse: Authentication error: Langfuse client initialized without public_key. Client will be disabled. Provide a public_key parameter or set LANGFUSE_PUBLIC_KEY environment variable.
```

Из-за этого:
- **процент валидных Langfuse-traces в этом прогоне измерить нельзя** (Cloud не подключён);
- известная проблема «~33% root trace loss на back-to-back диалогах» (см. changelog `2026-05-10-wsrunner-eval-observability.md`) в этом прогоне не верифицирована — для её проверки нужен повторный прогон с настроенным Langfuse Cloud;
- архитектурный fix PR #58 (`force_flush revert + post-span flush`) попадает в прогон по коду, но без Langfuse Cloud его эффект на trace-deliverability не проявляется.

### Среда наблюдения

- JSONL backup tracer пишет в `metrics/runs/<utc-ts>/trace.jsonl` отдельные dir-ы (от smoke), но для полного прогона `trace.jsonl` не накопил per-dialogue spans — нужна проверка `observability/tracer.py`. На текущий прогон tracer дал ровно 1 trace dir 16:42 (от smoke), полный прогон в JSONL не записан. **Это отдельный observability-баг, требует расследования** (видимо tracer привязан к `OBSERVABILITY_RUN_DIR` env, который для длинного прогона не переустанавливается).

### Известные baseline-флажки

- `applicable_counts` v2.3.5+ (success=90/100, refusal=19/100) могут отличаться от v1.0 — состав dataset мог измениться между версиями (добавились `held_out`, `adversarial`, `unknown_with_hypothesis`). Сравнение «100 → 100» строго apples-to-apples требует фиксированного dataset; на текущей выборке формальный delta может маскировать смену состава.

## Известные баги, проявившиеся в прогоне

| Bug (память) | Подтверждение в прогоне |
|---|---|
| RAG/web gap в графе | `multi_tool` n=16, success=0.25, cite=0.143 |
| Multi-turn lost | `follow_up` n=8, success=0.25, cite=0.000 |
| Prompt injection в `e2e_0100` | диалог в timeout — ответ не получен, статус неясен |
| ESPO derived | требует проверки per-dialogue answer'ов (не сделано в текущем PR) |
| Routing «Brent→forecast» | `forecast` n=16 отработал лучше всех scenario; возможно частично закрыт |
| llm_disambiguate None | требует анализа Langfuse-trace — Cloud не подключён, отложено |

Новые наблюдения:
- **43 timeout > 360s, концентрированные в хвосте (e2e_0073–0100)** — критический latency/stability баг, не зафиксирован ранее. Подозрение: memory/connection leak в server.py / agent loop / chromadb client / native tracer.
- `web_only` n=10 имеет cite=0.125 и struct=0.125 — резкая просадка по сравнению с `forecast`/`rag_only`. Возможный недохват цитат в web-формате (`[title](url) — domain, web`).

## Что НЕ в этом PR (по scope)

Координатор явно ограничил scope этой сессии:
- регрессии не фикситься в коде пайплайнов/скиллов;
- README/REPORT.md/examples/docs/modules/ не трогаются;
- работа только с `docs/eval-results-v2.3.5.md` + `docs/changelog/2026-05-11-eval-rebaseline-v2.3.5.md`.

Регрессии (timeout-хвост, refusal, web_only cite) и observability-баг (tracer JSONL не пишет на длинном прогоне) — на отдельные follow-up задачи координатору.
