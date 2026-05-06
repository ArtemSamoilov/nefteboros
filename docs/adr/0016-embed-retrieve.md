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

### Retrieval — bi-encoder + cross-encoder

Двухэтапная схема:

**Этап 1 — bi-encoder retrieval (BGE-M3):**
- Эмбеддим query
- Cosine similarity по всем 802 векторам в Chroma
- Возвращаем top-30 кандидатов (cheap)
- Опционально применяем metadata-фильтры (регион, блок, язык)

**Этап 2 — cross-encoder reranker (`BAAI/bge-reranker-v2-m3`):**
- Принимает пары (query, chunk_text)
- Глубже понимает релевантность чем cosine bi-encoder
- Возвращает top-5 (configurable)
- Размер модели ~2.3 ГБ, инференс ~50 пар/сек на CPU (приемлемо для top-30)

Альтернативы:
- **Pure bi-encoder без reranker** — быстрее, но precision@5 ниже на 10-20%. Для нефтегаз-вопросов с тонкой семантикой (например «дисконт Urals к Brent» vs «спред WTI-Brent») reranker даёт ощутимый прирост.
- **LLM-rerank через kimi** — дороже, медленнее, такой же precision как cross-encoder.
- **Hybrid BM25 + dense** — BM25 хорошо работает на точных терминах (тикеры, аббревиатуры), но добавляет сложности; отложено в backlog v1.x.

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

## Ссылки

- ADR-0009: corpus strategy
- ADR-0010: Marker conversion
- ADR-0011: chunking + tagging
- BGE-M3 paper: <https://arxiv.org/abs/2402.03216>
- bge-reranker-v2-m3: <https://huggingface.co/BAAI/bge-reranker-v2-m3>
