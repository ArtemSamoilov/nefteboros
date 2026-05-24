# RAG retriever — hybrid sparse+dense эксперименты

> Прогон hybrid-retrieval'а (BM25 + dense) поверх baseline'а из `rag-full-eval-report.md`. См. ADR-0027.

- **Период:** 2026-05-24
- **Ветка/PR:** `feature/rag-hybrid-search`
- **Связанные ADR:** ADR-0016 (embed+retrieve), ADR-0027 (hybrid)
- **Датасет:** `datasets/rag_eval/v1.jsonl`, 95 semi-synthetic Q (EN 55 / RU 40)

---

## 0. Находка перед прогоном — какая коллекция «production»

При проверке индекса вскрылось расхождение с постановкой задачи:

| Коллекция | Эмбеддинги | chunk_hit@5 | Статус |
|---|---|---:|---|
| `nefteboros_corpus_v1` (802 чанка, живая) | RAW-текст, **без** heading-prefix | **0.653** | то, что реально лежит локально |
| `nefteboros_corpus_v2_heading` (DEFAULT в `store.py`) | heading-prefix (v2) | 0.779 (по отчёту) | **пустая локально** |

Прогон baseline'а на живой коллекции дал `chunk_hit@5 = 0.653`, `chunk_hit@1 = 0.326`, `chunk_MRR = 0.458`, `source_hit@5 = 0.979` — **точное совпадение с v1-baseline** из `rag-full-eval-report.md` §3.1. То есть локально доступны только raw-эмбеддинги; prefixed (v2, 0.779) не персистнуты.

**Следствие для методологии:** честное сравнение «hybrid vs production» требует v2-prefix эмбеддингов. BM25/fusion от версии dense не зависят, но **профиль выигрыша зависит** (prefix уже частично решает SAME_DOC_MISS/corporate — те же кейсы, куда бьёт BM25). Поэтому:
- §1 ниже — directional-прогон на **v1** (валидация пайплайна + вклад BM25 на raw-dense);
- §3 — production-сравнение на **v2** после пересборки на GPU (на MPS-Mac ~3-4ч, на NVIDIA ~12 мин — `scripts/eval/run_hybrid_eval.sh`).

---

## 1. Directional — hybrid на v1 (raw dense)

Конфиги: `dense` (bi), `+hybrid` (RRF, k=60), `+hybrid-weighted` (alpha=0.5). k_dense=30, k_sparse=30, k_final=10.

### Overall

| Метрика | dense | +RRF | Δ RRF | +weighted | Δ wtd |
|---|---:|---:|---:|---:|---:|
| chunk_hit@1 | 0.326 | 0.400 | +7.4 | 0.463 | +13.7 |
| chunk_hit@3 | 0.526 | 0.663 | +13.7 | 0.716 | +19.0 |
| **chunk_hit@5** | **0.653** | **0.747** | **+9.4** | **0.811** | **+15.8** |
| chunk_hit@10 | 0.737 | 0.832 | +9.5 | 0.842 | +10.5 |
| chunk_MRR | 0.458 | 0.546 | +8.8 | 0.604 | +14.6 |
| source_hit@5 | 0.979 | 0.968 | −1.1 | 0.979 | 0.0 |

### По языку (chunk_hit@5)

| Slice | dense | +RRF | Δ | +weighted | Δ |
|---|---:|---:|---:|---:|---:|
| EN (n=55) | 0.727 | 0.764 | +3.7 | 0.836 | +10.9 |
| **RU (n=40)** | **0.550** | **0.725** | **+17.5** | 0.775 | +22.5 |

### По блоку (chunk_hit@5, dense → RRF)

| Блок | dense | +RRF | Δ |
|---|---:|---:|---:|
| 1_strategy (n=39) | 0.769 | 0.846 | +7.7 |
| **2_corporate (n=28)** | **0.429** | **0.643** | **+21.4** |
| 3_operational (n=17) | 0.588 | 0.588 | 0.0 |
| 4_geopolitics (n=11) | 0.909 | 0.909 | 0.0 |

**Выводы directional:**
1. Пайплайн работает end-to-end; BM25 даёт ощутимый прирост ровно там, где предсказано: **RU (+17.5)** и **corporate (+21.4)** — лексический матч точных термов/чисел спасает то, что dense на RU/таблицах путал.
2. `geopolitics`/`operational` без изменений — там dense уже силён (0.909) либо мало данных.
3. `source_hit@5` под RRF слегка просел (−1.1): BM25 иногда поднимает чужой source с высоким overlap.

---

## 2. Ловушка — почему weighted «выигрывает» (и почему это не сигнал)

weighted (0.811) > RRF (0.747) на overall. Соблазнительно сделать weighted дефолтом — **не делаем**, и вот почему.

Датасет synthetic: вопросы сгенерены LLM **по тексту чанков** → лексика вопроса почти копирует целевой чанк → BM25-скор целевого **пиковый и хорошо отделён** от остальных. weighted-слияние сохраняет магнитуду этого пика и потому эксплуатирует bias напрямую. RRF смотрит только на ранг (целевой = rank 1 → вклад 1/61 независимо от того, насколько он оторвался) — и поэтому **устойчивее к bias'у**.

На реальных запросах (перефразировки, чужая лексика, синонимы) BM25-распределение станет более плоским; магнитудное преимущество weighted схлопнется или станет вредным (переобучение под peaky-кейсы). RRF деградирует мягче. Поэтому:
- **production-default — RRF**;
- высокий weighted-результат трактуем как **меру bias'а датасета**, а не как причину переключаться;
- честная оценка обоих — на manual eval-сете (backlog).

Это та самая ловушка из постановки задачи: hybrid даёт иной профиль на реальных запросах, чем на synthetic. Не оверфитим.

---

## 3. Production — hybrid на v2 (heading-prefix)

**PENDING** — пересборка `nefteboros_corpus_v2_heading` на машине с GPU.

`<!-- TODO(gpu-run): заполнить из metrics/runs/*_rag_{dense_v2,hybrid_rrf,hybrid_weighted}_*.json -->`

| Конфиг | chunk_hit@5 | chunk_hit@1 | chunk_MRR | source_hit@5 |
|---|---:|---:|---:|---:|
| dense@v2 (baseline) | _0.779 ожид._ | 0.347 ожид. | 0.527 ожид. | 0.989 ожид. |
| +hybrid RRF | _TODO_ | _TODO_ | _TODO_ | _TODO_ |
| +hybrid weighted | _TODO_ | _TODO_ | _TODO_ | _TODO_ |

Слайсы RU/EN и corporate — заполнить из JSON.

**Гипотеза:** прирост hybrid на v2 **меньше**, чем на v1, т.к. heading-prefix уже поднял corporate (0.429→0.607) и table_only — частично те же кейсы, куда бьёт BM25. Если hybrid@v2 > 0.779 — цель достигнута; если ≈ 0.779 — фиксируем diminishing returns честно.

---

## 4. Воспроизводимость

```bash
# На машине с GPU (CUDA-torch). data/chunks/*.jsonl должны присутствовать (gitignored!).
git checkout feature/rag-hybrid-search
pip install -e . -r requirements-domain.txt   # +rank_bm25, pymorphy3, pymorphy3-dicts-ru
bash scripts/eval/run_hybrid_eval.sh           # build v2 + dense/hybrid-rrf/hybrid-weighted + failure analysis

# Локально на v1 (raw dense, без пересборки):
NEFTEBOROS_RAG_COLLECTION=nefteboros_corpus_v1 \
  python scripts/eval/eval_rag.py --version v1 --config bi+hybrid
```

Кэш токенизации — `data/sparse_index/sparse_tokens.json` (ключ: `TOKENIZER_VERSION` + id:len чанков); инвалидируется автоматически при правке корпуса/токенайзера.
