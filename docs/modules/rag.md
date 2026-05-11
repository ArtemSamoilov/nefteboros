# Модуль: RAG (retrieval-augmented generation)

Поиск релевантных фрагментов по отраслевому корпусу (OPEC MOMR, IEA OMR, EIA STEO и пр.). Многоязычные эмбеддинги BGE-M3 → плотный поиск в ChromaDB → фильтр по теме → переранжирование.

## Точка входа

- `nefteboros/rag/retriever.py:214` — `Retriever.retrieve(query, top_k, …) -> list[Chunk]`.
- Точка входа на уровне навыка: `skills/neftegaz_analyst/plugin.py:331` — `@traced_tool(name="rag_search")` (вызывается из Ouroboros).
- Внутри графа RAG напрямую не вызывается; используется как **внешний инструмент** до того, как агент решит обратиться к `analyst_query`. См. [analyst_graph.md](analyst_graph.md).

## Поток

1. `embedder.embed_query(query)` — BGE-M3 многоязычный (CPU/CUDA).
2. `store.vector_search(embedding, top_k_pre_rerank)` — плотный поиск ChromaDB, фильтр по теме и языку.
3. `retriever.rerank(query, candidates)` — cross-encoder переранжирование → финальный top-K.

## Входы / выходы

**Вход:** `query: str`, `top_k: int`, опционально `lang_filter` / `topic_filter`.

**Выход:** `list[Chunk]` — каждый чанк имеет `text`, `source` (`OPEC MOMR март 2026` и т. п.), `score`, `metadata` (страница, секция). Источники потребляются `synthesize` для генерации цитат вида `[Отчёт OPEC MOMR, март 2026]` (ADR-0019).

## Корпус

- Источники, чанкинг, тегирование — [ADR-0009](../adr/0009-corpus-strategy.md), [ADR-0011](../adr/0011-chunking-and-tagging.md).
- Преобразование PDF → markdown — [ADR-0010](../adr/0010-pdf-to-markdown-marker.md).
- Индексация: `python -m scripts.build_index` (читает PDF из `data/corpus/`, пишет в ChromaDB `data/vectorstore/`).
- Подготовительные шаги: `scripts/fetch_corpus.py`, `scripts/convert_corpus.py`, `scripts/chunk_corpus.py`.

## Ключевые ADR

- [ADR-0009](../adr/0009-corpus-strategy.md) — стратегия корпуса.
- [ADR-0010](../adr/0010-pdf-to-markdown-marker.md) — конвертация PDF.
- [ADR-0011](../adr/0011-chunking-and-tagging.md) — чанкинг и теги.
- [ADR-0016 (embed-retrieve)](../adr/0016-embed-retrieve.md) — embed + retrieve. **Коллизия:** существует второй `0016-forecast-skill.md`. Сигнал координатору.
- [ADR-0018](../adr/0018-rag-search-tool.md) — RAG как инструмент навыка.

## Метрики

**Инструментация есть на 3 этапа:**

| Этап | Span | as_type | Файл |
|---|---|---|---|
| Эмбеддинг запроса | `embed_query` | embedding | `nefteboros/rag/embedder.py:85` |
| Векторный поиск | `vector_search` | retriever | `nefteboros/rag/store.py:101` |
| Переранжирование | `rerank` | retriever | `nefteboros/rag/retriever.py:88` |

Корневой span инструмента `rag_search` — `skills/neftegaz_analyst/plugin.py:331`.

**Eval-скрипт:** `scripts/eval/eval_rag.py`:
- Метрики: `chunk_hit@1/3/5/10`, `source_hit@1/3/5/10`, `chunk_MRR`, `source_MRR`.
- Срезы: по языку (ru/en), по блоку (strategy/corporate/operational/geopolitics).
- Датасет: `datasets/rag_qa.jsonl`.
- Вывод: `metrics/runs/<date>_rag_<config>_<commit>.json`.

**Анализ ошибок:** `scripts/eval/analyze_rag_failures.py` — отдельный набор инструментов для разбора промахов.

## Известные ограничения

- Без BGE-переранжировщика качество падает заметно (см. дизайн в [docs/experiments/design.md](../experiments/design.md)).
- В режиме `--mock` eval ChromaDB подменяется заглушкой в памяти; результаты не сопоставимы с prod.
