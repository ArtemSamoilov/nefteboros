# Модуль: RAG (retrieval-augmented generation)

Поиск релевантных фрагментов по отраслевому корпусу (OPEC MOMR, IEA OMR, EIA STEO и пр.). BGE-M3 multilingual embeddings → ChromaDB dense search → topic filter → rerank.

## Точка входа

- `nefteboros/rag/retriever.py:214` — `Retriever.retrieve(query, top_k, …) -> list[Chunk]`.
- Skill-level tool entry: `skills/neftegaz_analyst/plugin.py:331` — `@traced_tool(name="rag_search")` (вызывается из Ouroboros tool dispatch).
- Внутри графа RAG напрямую не вызывается; используется как **внешний tool** до того как агент решит обратиться к `analyst_query`. См. [analyst_graph.md](analyst_graph.md).

## Поток

1. `embedder.embed_query(query)` — BGE-M3 multilingual (CPU/CUDA).
2. `store.vector_search(embedding, top_k_pre_rerank)` — ChromaDB dense, фильтр по топику/языку.
3. `retriever.rerank(query, candidates)` — cross-encoder rerank → топ-K финальный.

## Входы / выходы

**Вход:** `query: str`, `top_k: int`, optional `lang_filter` / `topic_filter`.

**Выход:** `list[Chunk]` — каждый chunk имеет `text`, `source` (`OPEC MOMR март 2026` и т. п.), `score`, `metadata` (страница, секция). Источники потребляются `synthesize` для генерации цитат `[Отчёт OPEC MOMR, март 2026]` (ADR-0019).

## Корпус

- Источники, чанкирование, тэгирование — [ADR-0009](../adr/0009-corpus-strategy.md), [ADR-0011](../adr/0011-chunking-and-tagging.md).
- Преобразование PDF → markdown — [ADR-0010](../adr/0010-pdf-to-markdown-marker.md).
- Индексация: `python -m scripts.build_index` (читает PDF из `data/corpus/`, пишет в ChromaDB `data/vectorstore/`).
- Подготовительные шаги: `scripts/fetch_corpus.py`, `scripts/convert_corpus.py`, `scripts/chunk_corpus.py`.

## Ключевые ADR

- [ADR-0009](../adr/0009-corpus-strategy.md) — стратегия корпуса.
- [ADR-0010](../adr/0010-pdf-to-markdown-marker.md) — конверсия PDF.
- [ADR-0011](../adr/0011-chunking-and-tagging.md) — чанкинг и тэги.
- [ADR-0016 (embed-retrieve)](../adr/0016-embed-retrieve.md) — embed + retrieve. **Коллизия:** существует второй `0016-forecast-skill.md`. Сигнал для координатора.
- [ADR-0018](../adr/0018-rag-search-tool.md) — RAG как skill tool.

## Метрики

**Есть инструментация на 3 этапа:**

| Этап | Span | as_type | Файл |
|---|---|---|---|
| Embedding запроса | `embed_query` | embedding | `nefteboros/rag/embedder.py:85` |
| Vector search | `vector_search` | retriever | `nefteboros/rag/store.py:101` |
| Rerank | `rerank` | retriever | `nefteboros/rag/retriever.py:88` |

Корневой tool span `rag_search` — `skills/neftegaz_analyst/plugin.py:331`.

**Eval скрипт:** `scripts/eval/eval_rag.py`:
- Метрики: `chunk_hit@1/3/5/10`, `source_hit@1/3/5/10`, `chunk_MRR`, `source_MRR`.
- Slices: по языку (ru/en), по блоку (strategy/corporate/operational/geopolitics).
- Датасет: `datasets/rag_qa.jsonl`.
- Output: `metrics/runs/<date>_rag_<config>_<commit>.json`.

**Failure analysis:** `scripts/eval/analyze_rag_failures.py` — отдельный toolkit для разбора промахов.

## Известные ограничения

- Без BGE reranker качество падает заметно (см. design [docs/experiments/design.md](../experiments/design.md)).
- На `--mock` режиме eval ChromaDB подменяется in-memory stub'ом; результаты не сопоставимы с prod.
