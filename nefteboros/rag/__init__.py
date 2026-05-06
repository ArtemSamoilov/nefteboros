"""RAG-пайплайн: PDF → Markdown → чанки с метаданными → BGE-M3 → ChromaDB → retriever.

Модули:
  - convert.py    — PDF → Markdown через Marker (PR A, ADR-0010)
  - chunker.py    — heading-aware разбивка MD с табличной спецлогикой (PR B, ADR-0011)
  - tagger.py     — source/section/topic теги через kimi-k2p6 (PR B, ADR-0011)
  - schema.py     — Pydantic Chunk + TopicTags
  - topic_vocabulary.py — закрытый словарь 5 осей × 22 значения
  - embedder.py   — BGE-M3 эмбеддинги (PR C, ADR-0016)
  - store.py      — ChromaDB persist + upsert идемпотентно (PR C, ADR-0016)
  - retriever.py  — bi-encoder retrieval + bge-reranker-v2-m3 (PR C, ADR-0016)

Tool wrapper для агента — отдельный PR feature/skill-integration.
"""
