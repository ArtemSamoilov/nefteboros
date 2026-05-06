# ADR-0016 — Эмбеддинги, vector store и retrieval

- **Дата:** 2026-05-06
- **Статус:** Принято
- **Контекст:** PR `feature/rag-embed-retrieve` (этап 3 из 3 в RAG-pipeline)
- **Связано:** ADR-0009 (corpus), ADR-0010 (Marker), ADR-0011 (chunking + tagging), будущий ADR на skill-integration

## Контекст

После PR B (`feature/rag-chunk`) у нас 802 тегированных чанка в `data/chunks/<source_id>.jsonl`. Задача PR C — превратить их в **поискаемое представление** и собрать pipeline для запросов от агента: embed → store → retrieve → rerank.

Требования:
1. Multilingual эмбеддинги (RU + EN, ~50/50 в корпусе)
2. Поддержка metadata-фильтров (например `region=russia AND geopolitics=sanctions`)
3. Top-k retrieval с осмысленным ранжированием
4. Воспроизводимость (CLI для сборки индекса с нуля)
5. Локальный запуск (без внешних API за исключением HydraGPT, которое уже есть)

## Решение

### Эмбеддинги — BGE-M3

Модель: `BAAI/bge-m3` через `sentence-transformers`.

| Свойство | Значение |
|---|---|
| Multilingual | 100+ языков, RU и EN на одном уровне с топ-моделями |
| Размерность | 1024 |
| Max sequence length | 8192 токенов (наш max chunk = 4031, запас 2×) |
| Mode | Dense (стандартный bi-encoder) |
| Размер модели | ~2.3 ГБ, грузится при первом use в `~/.cache/huggingface/` |
| Скорость на CPU | ~5-10 чанков/сек (подходит для one-time build, ~2-3 минуты на 802 чанка) |

Альтернативы:
- **OpenAI text-embedding-3-large** — 3072-dim, отличное качество. Отвергнут: требует OpenAI API (нет в нашем стеке), стоимость, vendor lock.
- **GigaChat embeddings** — Sber-native, было бы политически правильно. Отвергнут: API не стабилен, размерность/качество хуже BGE-M3 на multilingual benchmarks.
- **multilingual-e5-large** — 1024-dim, но BGE-M3 обходит его на BEIR/MIRACL ru.
- **Локальный mE5-instruct** — больше модель (560M), маргинальный прирост.

### Vector store — ChromaDB persistent

Модель: `chromadb.PersistentClient(path="data/vectorstore/")`.

Один collection на корпус: `nefteboros_corpus_v1` (версионирование в имени — при пересборке с другими параметрами создаём v2 без потери v1).

ID чанка = `chunk.id` (формат `{source_id}__{chunk_idx:04d}`) — детерминированно, идемпотентно при повторном `upsert`.

Metadata в Chroma — flat dict (Chroma не принимает nested). Сериализация через `Chunk.chroma_metadata()`:
- source_id, source_title, publisher, block, type, language, date
- section_path, page_start, page_end (number)
- has_table, is_table_only (bool)
- topic_energy, topic_market_aspect, topic_geopolitics, topic_finance, topic_region (comma-separated)

Запросы с метадата-фильтрами через нативный Chroma `where={"language": "ru", "block": "1_strategy"}`.

Альтернативы:
- **Qdrant** — производительнее на больших корпусах, но 802 чанка не нагрузка
- **FAISS** — нет встроенных metadata-фильтров, нужна обёртка
- **Weaviate** — оверкилл для нашего размера, отдельный сервис

### Retrieval — bi-encoder, reranker опционален

Изначально планировалась двухэтапная схема (bi-encoder → cross-encoder reranker). После калибровки на NVIDIA GPU 8 ГБ и CPU-бенча — **reranker отключён в default** из-за server constraints (см. секцию «Calibration» ниже). Bi-encoder retrieval достаточен для baseline, reranker остаётся доступным для off-server eval.

**Default — bi-encoder only:**
- Эмбеддим query через BGE-M3
- Cosine similarity по векторам в Chroma → top-5 (configurable через `k_final`)
- Metadata-фильтры (регион, блок, язык) применяются на уровне Chroma `where`

**Опционально — cross-encoder reranker** (`BAAI/bge-reranker-v2-m3`):
- Включается через `Retriever.retrieve(rerank=True)` или env `NEFTEBOROS_RETRIEVAL_RERANK=true`
- Берёт top-30 от bi-encoder, переоценивает, возвращает top-5
- Только off-server (нужен GPU 12+ ГБ или мощный CPU; на 8 ГБ GPU две модели не помещаются — VRAM 7962/8192, GPU 100%, заметная деградация)

Альтернативы для будущего production reranker'а (backlog v1.x):
- **LLM-rerank через kimi-k2p6** через HydraGPT — HTTP-вызов, ~3-5 сек latency, не требует памяти на сервере
- **`bge-reranker-base`** (278M, ~600 МБ) — в 4× легче v2-m3, multilingual, не тестировали
- **Hybrid BM25 + dense** — для точных терминов (тикеры, аббревиатуры)

## Архитектура

```
nefteboros/rag/
  embedder.py       — BGE-M3 wrapper:
                        Embedder.embed(texts: list[str]) -> list[list[float]]
                        Embedder.embed_query(text: str) -> list[float]
                        Singleton (модель грузится один раз)
  store.py          — ChromaDB wrapper:
                        VectorStore.upsert(chunks: list[Chunk])
                        VectorStore.search(query_emb, k, where) -> list[(Chunk, score)]
                        VectorStore.count() -> int
                        Создаёт/открывает PersistentClient
  retriever.py      — высокоуровневый retriever:
                        Retriever.retrieve(query, k_dense=30, k_final=5, where=...) -> list[(Chunk, rerank_score)]
                        Объединяет embedder + store + reranker
                        Lazy-load reranker (отдельная модель ~2.3 ГБ)
scripts/
  build_index.py    — CLI: data/chunks/*.jsonl → data/vectorstore/
                        --force перепересборка с нуля
                        --only фильтр по source_id
                        Прогресс по batch'ам
tests/
  test_rag_smoke.py — smoke на 5 запросов (по одному на демо-сценарий ТЗ),
                        проверяет что retrieval возвращает релевантный source
```

## Конфигурация

Default параметры (изменяемые в API/CLI):
- `EMBEDDING_MODEL = "BAAI/bge-m3"`
- `RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"`
- `COLLECTION_NAME = "nefteboros_corpus_v1"`
- `K_DENSE = 30` (после bi-encoder)
- `K_FINAL = 5` (после reranker)
- Embedding batch size = 16 (CPU-friendly)

ENV-переопределения через `os.environ`:
- `NEFTEBOROS_RAG_COLLECTION` — имя коллекции (для тестов)
- `NEFTEBOROS_RAG_VECTORSTORE_PATH` — путь к persist-каталогу (default `data/vectorstore/`)

## Идемпотентность

`build_index.py` проверяет существующую коллекцию:
- Если её нет — создаёт, embeds, upserts всё
- Если есть — сравнивает existing IDs с manifest chunks; апсёртит дельту, удаляет «orphans»
- `--force` — drop collection и собирает с нуля

Это позволяет переcборку при изменении одного документа без полного reembed.

## Что НЕ в этом PR

- **Tool wrapper для агента** (`skills/neftegaz_analyst/` интеграция) → отдельный PR `feature/skill-integration`
- **Hybrid BM25 + dense retrieval** → backlog v1.x, при необходимости
- **Eval RAG-метрик** (precision@k, MRR, recall на 20-50 синтетических Q + ground truth) → отдельный PR `feature/eval-rag`
- **Query rewriting / HyDE** (LLM-генерация гипотетических документов перед retrieval) → backlog v1.x
- **Кеширование query-embeddings** → optional, добавим если будет нужно для performance

## Альтернативы рассмотренные

- **Без reranker** — отвергнуто, см. выше.
- **Pure FAISS вместо Chroma** — Chroma даёт metadata-фильтры из коробки, FAISS требует отдельной реализации.
- **Embedding+rerank в одной модели** (например cohere-rerank) — vendor lock, не self-hosted.
- **Sparse-only retrieval (BM25)** — недостаточно для нашего корпуса с разнообразием формулировок (RU/EN).

## Calibration — фактические замеры

### Build_index на NVIDIA GPU 8 ГБ (off-server)
- BGE-M3, batch=8, max_seq=4096
- Загрузка модели: ~90 сек
- Embedding 802 чанков: ~660 сек (~11 мин), 1.2 chunk/sec
- VRAM: ~2.3 ГБ
- Upsert в Chroma: ~3 сек
- **Итого: ~12 мин** ✓

### Reranker rerank-pass на NVIDIA GPU 8 ГБ — **не работает**
- BGE-M3 (~2.3 ГБ) + bge-reranker-v2-m3 (~2.3 ГБ) = ~4.6 ГБ + CUDA overhead
- VRAM забит на 7962/8192 МБ, GPU 100%, температура 79°C
- 6 smoke-тестов (1 query каждый) висят 30+ мин без видимого прогресса
- **Вывод:** для inference с reranker'ом нужен GPU 12+ ГБ, либо отказаться от reranker'а

### Query embedding на CPU (Mac M-series, 10 cores)
- Платформа: Darwin arm64, без CUDA
- Загрузка модели: 7.7 сек
- RSS после загрузки: 974 МБ
- Warmup query: 2.4 сек
- 5 production-style queries: median **89 мс**, mean 102 мс, max 174 мс
- RSS финальный: **2094 МБ**

### Прогноз для сервера 2 vCPU / 4 ГБ RAM (Timeweb)
Линейная экстраполяция, 2 vCPU vs 10 cores, без бенчмарка:
- Cold model load: ~20-25 сек (один раз при старте сервиса)
- Query latency: ~300-500 мс (warm)
- RSS: ~2 ГБ под BGE-M3 + ~500 МБ под Ouroboros core ≈ 2.5 ГБ из 4 → **уложится** ✓
- Reranker: не используется

## Ссылки

- ADR-0009: corpus strategy
- ADR-0010: Marker conversion
- ADR-0011: chunking + tagging
- BGE-M3 paper: <https://arxiv.org/abs/2402.03216>
- bge-reranker-v2-m3: <https://huggingface.co/BAAI/bge-reranker-v2-m3>
