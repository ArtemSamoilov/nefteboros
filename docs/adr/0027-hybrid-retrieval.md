# ADR-0027 — Hybrid sparse+dense retrieval (BM25 + RRF)

- **Дата:** 2026-05-24 (production-цифры на v2-prefix — 2026-05-25)
- **Статус:** Принято
- **Контекст:** PR `feature/rag-hybrid-search`. Реализация backlog-пункта из ADR-0016 и `rag-full-eval-report.md` §8.
- **Связано:** ADR-0011 (chunking + tagging), ADR-0016 (embed + retrieve), `docs/experiments/rag-full-eval-report.md`, `docs/experiments/rag-hybrid-experiments.md`

## Контекст

Ретривер был **чисто dense**: BGE-M3 → ChromaDB cosine top-k → опц. topic-filter → опц. cross-encoder rerank (выключен). Лексической компоненты не было.

Failure analysis baseline'а (`rag-full-eval-report.md` §3.5) показал главное узкое место: **SAME_DOC_MISS = 21.1%** — правильный документ найден (`source_hit@5 = 0.989`), но внутри него выше ранжируется не тот чанк. Это классическая зона лексического сигнала: точные термы (тикеры, имена компаний, числовые показатели, `section_path`) различают соседние чанки одного документа лучше, чем семантический эмбеддинг, который видит их как «похожие». Слабее всего — `2_corporate` (chunk_hit@5 = 0.607 на v2) и **RU** (0.675 vs EN 0.855): русская морфология размывает dense-сигнал, а в корпоративке много числовых таблиц.

Цель: добавить sparse-retrieval, слить с dense, поднять `chunk_hit@5` над baseline.

## Решение

### 1. Sparse-метод — BM25 (`rank_bm25`), НЕ нативный BGE-M3 sparse

Развилка стояла между (а) BM25 над текстом чанков и (б) нативными sparse-векторами BGE-M3 (модель multi-functional: dense + sparse + ColBERT одной сетью). Элегантность (б) — «одна модель» — на практике не выдерживает наших ограничений:

| Критерий | BM25 (выбран) | BGE-M3 native sparse |
|---|---|---|
| Память | in-memory индекс ~13 МБ, без модели | требует `FlagEmbedding.BGEM3FlagModel`: либо **2-я копия модели ~2.3 ГБ** рядом с dense-эмбеддером, либо замена `SentenceTransformer`-обёртки (non-goal «не трогать embedding») |
| 4 ГБ сервер | помещается (как и было до reranker'а) | тот же бюджет, что **уже убил cross-encoder reranker** (ADR-0016 §Calibration) |
| Интеграция с Chroma | независимый индекс, fusion по id | Chroma в нашей версии **не хранит/не ищет sparse нативно** → всё равно руками строить sparse-dot индекс |
| RU-морфология | решается лемматизацией (см. §3) | subword-токенизатор устойчив «из коробки» — единственный реальный плюс (б) |
| Backlog | прямо назван в ADR-0016 и отчёте | — |

Итог: «одна модель» **не экономит интеграцию** (sparse-индекс всё равно строить руками), но добавляет ровно ту память, которую проект бережёт. Единственное преимущество (б) — устойчивость к RU-морфологии — закрываем лемматизацией на стороне BM25. Берём BM25.

> Прим.: ADR-0016 §«Альтернативы рассмотренные» отвергал **sparse-only** BM25 («недостаточно для RU/EN разнообразия формулировок»). Это решение не противоречит: мы добавляем BM25 не вместо dense, а **в дополнение** — hybrid, где dense несёт семантику, sparse — точные термы.

### 2. Слияние — Reciprocal Rank Fusion (default), weighted — аблация

RRF: `score(d) = Σ_r 1/(k + rank_r(d))`, `k = 60`.

Cosine-similarity BGE-M3 живёт в ~[0.3, 0.9], BM25-скор — unbounded и зависит от корпуса/длины запроса. Складывать их напрямую нельзя; weighted-слияние требует **per-query min-max нормализации**, которая хрупка и переобучается под распределение скоров конкретного eval-сета. RRF работает **только по рангам** — scale-free, один робастный гиперпараметр.

Weighted-слияние (`alpha·norm(dense) + (1-alpha)·norm(sparse)`) реализовано и прогоняется как **аблация** — см. §Ловушка: на synthetic-датасете weighted даёт цифры ВЫШЕ RRF, но это, вероятно, артефакт bias'а, а не реальное превосходство. Production-default — RRF.

### 3. RU-токенизация — лемматизация pymorphy3 + стоп-слова

Наивный whitespace-BM25 на русском плох: `нефть / нефти / нефтью / нефтяной` без нормализации — разные термы, лексический матч разваливается. Пайплайн (`text_norm.tokenize`):

1. токенизация по `\w+` (unicode: кириллица + латиница + цифры);
2. lowercase;
3. **лемматизация кириллических токенов** через pymorphy3 (`нефти → нефть`, `санкциями → санкция`);
4. отброс RU/EN стоп-слов и одиночных букв; **цифры сохраняются** (годы, цены, объёмы — критичный сигнал для финансовых запросов).

pymorphy3 — опциональная зависимость: при отсутствии деградируем до lower+токенизация (это «минимум» из ТЗ), логируем один раз. Латиница не лемматизируется (морфология лёгкая, dense несёт семантику).

### 4. Флаг — `NEFTEBOROS_HYBRID=off` по умолчанию

Production-safe, ровно как reranker: новый путь не включается без явного `NEFTEBOROS_HYBRID=on`. Прод-дефолт не меняется до подтверждённых v2-цифр.

## Архитектура

```
nefteboros/rag/
  text_norm.py     — tokenize(text) -> list[str]; RU/EN, pymorphy3-лемматизация,
                       стоп-слова; TOKENIZER_VERSION для инвалидации кэша
  sparse_index.py  — SparseIndex (singleton): BM25Okapi над 802 чанками из
                       data/chunks; id-aligned search(query,k)->[SparseHit];
                       дисковый кэш токенов (data/sparse_index/sparse_tokens.json)
  fusion.py        — reciprocal_rank_fusion(rankings,k=60); weighted_score_fusion
  retriever.py     — retrieve(..., hybrid, fusion, rrf_k, alpha, k_sparse):
                       dense pool + sparse → _hybrid_fuse → topic-filter? → rerank? → top-k
scripts/eval/
  eval_rag.py      — --config bi+hybrid / bi+hybrid-weighted (+ --k-sparse/--rrf-k/--alpha)
  run_hybrid_eval.sh — сборка v2-индекса + before/after на машине с GPU
tests/test_rag_hybrid.py — 13 тестов (токенизация, RRF-математика, fusion-glue, BM25 self-retrieval)
```

Слияние происходит ДО topic-filter/rerank: hybrid формирует пул кандидатов, остальной pipeline без изменений. При активном metadata-`where` sparse-only чанки (вне dense-пула) отбрасываются — dense уже отфильтрован по `where`, не тащим обратно отрезанное.

## Конфигурация (ENV)

| Переменная | Default | Назначение |
|---|---|---|
| `NEFTEBOROS_HYBRID` | `off` | вкл hybrid (`on`/`true`/`1`) |
| `NEFTEBOROS_HYBRID_FUSION` | `rrf` | `rrf` \| `weighted` |
| `NEFTEBOROS_HYBRID_RRF_K` | `60` | константа RRF |
| `NEFTEBOROS_HYBRID_ALPHA` | `0.5` | вес dense в weighted |
| `NEFTEBOROS_RETRIEVAL_K_SPARSE` | `30` | глубина BM25-списка |
| `NEFTEBOROS_SPARSE_CACHE_DIR` | `data/sparse_index` | кэш токенов |

## Результаты

### Directional на v1 (raw dense, без heading-prefix) — локальный прогон, 95-Q

> v1 — НЕ production. Локально живая коллекция оказалась raw-эмбеддингами (см.
> `rag-hybrid-experiments.md` §0). Прогон валидирует пайплайн и показывает вклад BM25.

| Конфиг | chunk_hit@5 | chunk_hit@1 | chunk_MRR | source_hit@5 |
|---|---:|---:|---:|---:|
| dense (bi) | 0.653 | 0.326 | 0.458 | 0.979 |
| **+hybrid RRF** | **0.747** (+9.4) | 0.400 | 0.546 | 0.968 |
| +hybrid weighted | 0.811 (+15.8) | 0.463 | 0.604 | 0.979 |

RU chunk_hit@5: dense 0.550 → RRF 0.725 (**+17.5**); corporate 0.429 → 0.643 (**+21.4**) — ровно те слайсы, где предсказана польза лексики.

### Production на v2 (heading-prefix, baseline 0.779) — 95-Q, commit `015e1bb`, 2026-05-25

| Конфиг | chunk_hit@5 | chunk_hit@1 | chunk_MRR | source_hit@5 |
|---|---:|---:|---:|---:|
| dense@v2 (baseline) | **0.779** | 0.347 | 0.527 | 0.989 |
| **+hybrid RRF** | **0.832** (+5.3) | 0.421 (+7.4) | 0.598 (+7.1) | 0.968 (−2.1) |
| +hybrid weighted | 0.863 (+8.4) | 0.516 (+16.9) | 0.657 (+13.0) | 0.979 (−1.0) |

Слайсы `chunk_hit@5` (dense → RRF → weighted):

| Slice | dense | +RRF | Δ RRF | +weighted | Δ wtd |
|---|---:|---:|---:|---:|---:|
| EN (n=55) | 0.855 | 0.782 | **−7.3** | 0.818 | −3.7 |
| RU (n=40) | 0.675 | 0.900 | **+22.5** | 0.925 | +25.0 |
| 1_strategy (n=39) | 0.897 | 0.846 | −5.1 | 0.846 | −5.1 |
| **2_corporate (n=28)** | 0.607 | 0.893 | **+28.6** | 0.929 | +32.2 |
| 3_operational (n=17) | 0.706 | 0.588 | −11.8 | 0.706 | 0.0 |
| 4_geopolitics (n=11) | 0.909 | 1.000 | +9.1 | 1.000 | +9.1 |

**Headline:** hybrid RRF поднимает `chunk_hit@5` с production-baseline `0.779` до **`0.832` (+5.3 п.п.)** — цель достигнута. RRF остаётся production-default (см. §Ловушка); weighted даёт +8.4, но это симптом synthetic-bias'а, не сигнал переключать.

**Гипотеза «на v2 прирост меньше, чем на v1» — НЕ подтвердилась.** Heading-prefix v2 **не закрыл** corporate-проблему: RRF здесь даёт +28.6 п.п. (на v1 было +21.4) — БОЛЬШЕ, чем на raw-dense. То же по RU: +22.5 на v2 против +17.5 на v1. BM25 и prefix целятся в один кейс (SAME_DOC_MISS), но прирост не вычитается — это **дополняющие сигналы**, не дублирующие.

**Что хуже на v2:** появилась направленная регрессия там, где dense уже близок к потолку — **EN −7.3 п.п.**, strategy −5.1, operational −11.8. На v1 такого паттерна не было: тогда BM25 поднимал слабые слайсы и не трогал сильные. На v2 dense выжал сильные слайсы (EN 0.855, strategy 0.897), и теперь BM25 в этих случаях иногда вытесняет правильный чанк лексически похожим. Это согласуется с −2.1 п.п. на overall `source_hit@5` (0.989 → 0.968). Малая регрессия, реальная, не маскируется выигрышем на overall — следить, при росте корпуса проверить, не растёт ли.

## Что НЕ в этом PR

- Включение hybrid в production-default (ждём подтверждённых v2-цифр — non-goal ТЗ).
- Reranker (вопрос памяти сервера, остаётся off).
- Изменения chunking/embedding.
- Тюнинг `k`/`alpha`/`k_sparse` (дефолты-стандарты; сетка — backlog).
- BGE-M3 native sparse (см. §Решение.1).

## Ловушка — synthetic bias датасета (важно для интерпретации)

95-Q сгенерены LLM по тексту чанков (`rag-full-eval-report.md` §9) → формулировки **лексически близки** к целевому чанку. Это **системно завышает BM25** (точный лексический overlap query↔target). Следствия:

1. Абсолютный прирост hybrid на этом сете — **верхняя оценка**; на реальных запросах (перефразировки, чужая лексика) profile будет иным и, вероятно, скромнее.
2. **weighted > RRF на этом сете — почти наверняка проявление bias'а, а не реальное превосходство.** Synthetic-вопросы дают BM25-скоры пиковые и хорошо разделённые; weighted эксплуатирует магнитуду, RRF (по рангам) её игнорирует. На реальных запросах магнитудное преимущество weighted схлопнется (или навредит — переобучение на peaky-распределение). Поэтому production-default — **RRF**, а высокий weighted-результат трактуем как симптом bias'а, не как сигнал переключаться.
3. Не оверфитим под этот сет: дефолты `k=60`/`alpha=0.5` — стандартные, не подобраны под 95-Q. Честная оценка hybrid требует manual eval-сета (backlog `rag-full-eval-report.md` §8.1).

## Альтернативы

- **BGE-M3 native sparse** — см. §Решение.1 (память + Chroma).
- **Weighted как default** — отвергнуто, см. §Ловушка.
- **Sparse-only (BM25)** — отвергнуто ещё в ADR-0016; hybrid сохраняет семантику dense.
- **HyDE / query rewriting** — ортогонально, backlog.

## Слабые места (саморазгром)

- **`source_hit@5` под RRF просел и на v1, и на v2** (v1: 0.979 → 0.968; v2: 0.989 → 0.968): BM25 иногда поднимает чужой source с высоким лексическим overlap, вытесняя правильный. Малый эффект, но воспроизводимый между версиями prefix'а — следить при росте корпуса.
- **Регрессия на «сильных» слайсах на v2** — EN −7.3 п.п., strategy −5.1, operational −11.8 (chunk_hit@5 под RRF). Цена за +28.6 на corporate. На v1 этой регрессии не было: heading-prefix поднял потолок dense на сильных слайсах, и BM25 теперь иногда лексически вытесняет правильный чанк. На текущем корпусе размер регрессии перекрывается ростом на слабых слайсах, но при росте en/strategy доли в реальном трафике гипотеза «hybrid выигрывает в среднем» может ослабнуть — нужен manual eval-сет.
- **Стоп-листы и `len<2`-фильтр захардкожены** в `text_norm`. Для корпуса ок; вынос в конфиг — при необходимости.
- **Кэш токенов инвалидируется по `(TOKENIZER_VERSION + id:len)`** — правка логики токенизации требует bump'а `TOKENIZER_VERSION`, иначе подхватится старый кэш. Задокументировано в коде.

## Ссылки

- ADR-0016 — embed + retrieve (предшественник, backlog hybrid)
- `docs/experiments/rag-hybrid-experiments.md` — детальный прогон
- BM25: Robertson & Zaragoza (2009); `rank_bm25`
- RRF: Cormack et al. (2009), "Reciprocal Rank Fusion outperforms Condorcet…"
- pymorphy3 — морфологический анализатор (OpenCorpora)
