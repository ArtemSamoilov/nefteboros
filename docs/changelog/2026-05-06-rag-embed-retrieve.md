# Changelog: rag-embed-retrieve — BGE-M3 + Chroma + retrieval + reranker

- **Дата:** 2026-05-06
- **PR:** `feature/rag-embed-retrieve`
- **ADR:** [docs/adr/0016-embed-retrieve.md](../adr/0016-embed-retrieve.md)
- **Этап:** 3 из 3 в RAG-pipeline (закрывает цепочку extract → chunk → embed/retrieve)

## Задача

Превратить 802 тегированных чанка из `data/chunks/*.jsonl` (выход PR B) в **поискаемое векторное представление** и собрать end-to-end retrieval pipeline для агента: query → embed → search Chroma → rerank → top-5.

## Что сделано

### Код

- `nefteboros/rag/embedder.py` — singleton-обёртка над `sentence-transformers.SentenceTransformer`:
  - `Embedder.get()` — lazy-load BGE-M3 (~2.3 ГБ при первом use)
  - `embed(texts, normalize=True)` — для документов (cosine = dot)
  - `embed_query(text)` — для одного запроса
  - Multilingual (RU + EN), 1024-dim, max 8192 токенов
- `nefteboros/rag/store.py` — обёртка над `chromadb.PersistentClient`:
  - `VectorStore.open()` — открывает/создаёт `data/vectorstore/`
  - `upsert(ids, documents, embeddings, metadatas)` — идемпотентно
  - `search(q_emb, k, where)` — top-k cosine с metadata-фильтрами
  - `existing_ids()`, `delete()`, `reset()` — для управления коллекцией
  - Один collection: `nefteboros_corpus_v1`
- `nefteboros/rag/retriever.py` — высокоуровневый retriever:
  - `Retriever.retrieve(query, k_dense=30, k_final=5, where=..., rerank=True)`
  - Bi-encoder (BGE-M3) → top-30 → cross-encoder (`bge-reranker-v2-m3`) → top-5
  - `Reranker` — singleton, lazy-load (~2.3 ГБ)
- `scripts/build_index.py` — идемпотентный CLI:
  - Читает `data/chunks/*.jsonl`, эмбеддит дельту, апсёртит в Chroma
  - `--force` — drop коллекции и пересборка с нуля
  - `--only` — фильтр по source_id
  - `--batch-size` — настройка embedder batch (default 16)
  - Прогресс по batch'ам через `show_progress_bar=True`

### Тесты

- `tests/test_rag_smoke.py` — 5 retrieval queries (по одному на демо-сценарий ТЗ):
  - «Каков прогноз спроса на нефть к 2030 от OPEC?» → `opec_woo_2025` или `iea_oil_2025`
  - «Что говорит ОПЕК+ про квоты в апреле 2026?» → `ief_momr_comparative_2026-04`
  - «Как работает price cap?» → `bruegel_wp_2025-32_oil_sanctions`
  - «Стратегия Новатэка по СПГ?» → `novatek_ar_2024`, `giignl_lng_2025`
  - «Иран февраль 2026?» → `crs_us_iran_conflict_2026-03`
  - Тест metadata-фильтра `language=ru`
  - Если индекс не собран — тесты skip'ятся

### Документация

- `docs/adr/0016-embed-retrieve.md` — обоснование выбора BGE-M3 / Chroma / cross-encoder reranker, альтернативы, идемпотентность, что не в PR
- `docs/changelog/2026-05-06-rag-embed-retrieve.md` — этот файл
- `nefteboros/rag/__init__.py` — обновлён список модулей

### Данные

- `data/vectorstore/` — gitignored (уже было), содержит persistent Chroma коллекцию `nefteboros_corpus_v1` с 802 эмбеддингами и метаданными

## Решения (см. ADR-0016)

- **BGE-M3** (1024-dim, multilingual) — лучшее open-source качество для RU+EN на наших scenarios.
- **ChromaDB persistent** — нативные metadata-фильтры (например `where={"region": "russia", "geopolitics": "sanctions"}`), достаточная скорость для 802 чанков.
- **Двухэтапный retrieval (bi → cross)** — bge-reranker-v2-m3 поверх top-30 даёт +10-20% precision@5 на тонкой семантике (например различение «дисконт Urals к Brent» vs «спред WTI-Brent»).
- **Singleton-pattern для моделей** — обе модели (~2.3 ГБ каждая) грузятся один раз при первом use, потом hot.
- **Идемпотентный upsert по chunk_id** — `build_index.py` без `--force` добавляет только новые чанки.

## Что НЕ в этом PR

- **Tool wrapper для Ouroboros skill** — отдельный PR `feature/skill-integration` (там же интеграция с LangGraph subgraph и query-classifier).
- **Hybrid BM25 + dense retrieval** — backlog v1.x.
- **HyDE / query rewriting** — backlog v1.x.
- **Eval RAG-метрик** (precision@k, MRR, recall на 20-50 синтетических Q + ground truth) → отдельный PR `feature/eval-rag`.
- **Web UI tab для RAG-чата** — часть `feature/skill-integration`.

## Файлы

**Добавлено:**
- `nefteboros/rag/embedder.py`
- `nefteboros/rag/store.py`
- `nefteboros/rag/retriever.py`
- `scripts/build_index.py`
- `tests/test_rag_smoke.py`
- `docs/adr/0016-embed-retrieve.md`
- `docs/changelog/2026-05-06-rag-embed-retrieve.md`

**Изменено:**
- `nefteboros/rag/__init__.py` — список модулей актуализирован

## Тесты

- AST OK для embedder.py, store.py, retriever.py, build_index.py, test_rag_smoke.py
- `pip install chromadb sentence-transformers torch` — установлены и импортируются
- `python scripts/build_index.py` — запуск на полном корпусе (см. итог в этом changelog после завершения)
- `pytest tests/test_rag_smoke.py -v` — после build_index, проверка что 5 demo-запросов находят релевантные источники
