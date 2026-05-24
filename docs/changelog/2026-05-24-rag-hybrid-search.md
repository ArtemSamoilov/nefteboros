# 2026-05-24 — Hybrid sparse+dense retrieval (BM25 + RRF)

**PR:** `feature/rag-hybrid-search`
**Связано:** [ADR-0027](../adr/0027-hybrid-retrieval.md), [ADR-0016](../adr/0016-embed-retrieve.md), [rag-full-eval-report](../experiments/rag-full-eval-report.md), [rag-hybrid-experiments](../experiments/rag-hybrid-experiments.md).

## Задача

Добавить лексическую (sparse) компоненту к чисто dense BGE-M3 ретриверу, слить с dense и поднять `chunk_hit@5` над baseline. Реализация backlog-пункта ADR-0016 / отчёта §8.

## Контекст

Главный failure mode baseline'а — **SAME_DOC_MISS = 21.1%**: правильный source найден (`source_hit@5 = 0.989`), но внутри него выше ранжируется не тот чанк. Это зона точных термов (тикеры, имена, числа, `section_path`), где лексический матч сильнее семантического. Слабее всего corporate и RU — там и ожидается польза.

## Что сделано

**Код:**
- `nefteboros/rag/text_norm.py` — `tokenize()`: RU/EN, лемматизация pymorphy3 (`нефти→нефть`), RU/EN стоп-слова, сохранение цифр; `TOKENIZER_VERSION` для кэша.
- `nefteboros/rag/sparse_index.py` — `SparseIndex` (singleton): `BM25Okapi` над 802 чанками из `data/chunks`, id-aligned `search()`, дисковый кэш токенизированного корпуса (лемматизация прогоняется один раз).
- `nefteboros/rag/fusion.py` — `reciprocal_rank_fusion(k=60)` + `weighted_score_fusion(alpha)`.
- `nefteboros/rag/retriever.py` — `retrieve(hybrid, fusion, rrf_k, alpha, k_sparse)`; `NEFTEBOROS_HYBRID=off` по умолчанию; `_hybrid_fuse` (dense+sparse, dense выигрывает метаданные; `restrict_ids` при активном `where`).
- `scripts/eval/eval_rag.py` — конфиги `bi+hybrid` / `bi+hybrid-weighted`, флаги `--k-sparse/--rrf-k/--alpha`, hybrid-блок в JSON-метриках.
- `scripts/eval/run_hybrid_eval.sh` — сборка v2-индекса + before/after на машине с GPU.
- `requirements-domain.txt` — `rank_bm25`, `pymorphy3`, `pymorphy3-dicts-ru` (опциональны для dense-only прода).

**Тесты** (`tests/test_rag_hybrid.py`, 13 шт.):
- токенизация: RU-лемматизация, стоп-слова, сохранение цифр, EN, одиночные буквы;
- fusion: детерминированный порядок RRF, missing-in-one, weighted с alpha, вырожденная нормализация;
- интеграция `Retriever._hybrid_fuse` на синтетических хитах (без модели/Chroma): sparse приносит нового кандидата; `restrict_ids` отбрасывает sparse-only;
- BM25 на реальном корпусе: self-retrieval (чанк в top-3 по своему тексту), убывание скоров.

**Docs:** ADR-0027, `rag-hybrid-experiments.md`, этот changelog.

## Метрики — directional на v1 (raw dense), 95-Q

| Конфиг | chunk_hit@5 | chunk_MRR | source_hit@5 |
|---|---:|---:|---:|
| dense (bi) | 0.653 | 0.458 | 0.979 |
| +hybrid RRF | **0.747** (+9.4 п.п.) | 0.546 | 0.968 |
| +hybrid weighted | 0.811 (+15.8) | 0.604 | 0.979 |

RU +17.5 п.п. (0.550→0.725), corporate +21.4 (0.429→0.643) на RRF.

**Production-сравнение на v2-prefix (baseline 0.779) — pending пересборки индекса на GPU** (`run_hybrid_eval.sh`; локально v2-коллекция оказалась пустой, MPS-пересборка ~4ч).

## Файлы

- **Добавлено:** `nefteboros/rag/{text_norm,sparse_index,fusion}.py`, `scripts/eval/run_hybrid_eval.sh`, `tests/test_rag_hybrid.py`, `docs/adr/0027-hybrid-retrieval.md`, `docs/experiments/rag-hybrid-experiments.md`, этот changelog.
- **Изменено:** `nefteboros/rag/retriever.py`, `scripts/eval/eval_rag.py`, `requirements-domain.txt`.
- **Удалено:** —

## Тесты

- `.venv` (Python 3.14): `pytest tests/test_rag_hybrid.py` — **13/13 зелёные** (6.4с). config=bi/hybrid не требует GigaChat, поэтому 3.14 ок для RAG-eval (в отличие от forecast/e2e — там `.venv312`).
- AST-parse всех затронутых `.py` — OK.
- `eval_rag.py --config bi / bi+hybrid / bi+hybrid-weighted` на v1 прогнаны (см. метрики выше).

## Что НЕ в PR (отложено явно)

- Включение hybrid в production-default — ждём v2-цифр (non-goal ТЗ «не менять default без подтверждённых цифр»).
- Reranker — off (память сервера).
- Тюнинг `k`/`alpha`/`k_sparse` — дефолты-стандарты, сетка в backlog.
- BGE-M3 native sparse — см. ADR-0027 §Решение.1.

## Слабые места (саморазгром)

- **v2-цифры ещё не получены.** Headline «hybrid > 0.779» пока не доказан — только directional на более слабом v1. Возможен diminishing returns (prefix уже забрал часть выигрыша). Зафиксируем честно после GPU-прогона.
- **weighted > RRF на synthetic — почти наверняка bias, не сигнал.** Вопросы сгенерены по тексту чанков → BM25 пиковый и разделённый → weighted эксплуатирует магнитуду. Production-default оставлен RRF (scale-free). Подробно в ADR-0027 §Ловушка.
- **source_hit@5 под RRF просел 0.979→0.968** — BM25 иногда поднимает чужой source с высоким overlap. Следить на v2.
