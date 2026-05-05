"""RAG-пайплайн: PDF → Markdown → чанки с метаданными → BGE-M3 → ChromaDB → retriever.

Содержит / будет содержать:
  - convert.py    — PDF → Markdown через Marker (PR A, ADR-0010)
  - chunker.py    — heading-aware разбивка MD с табличной спецлогикой (PR B, ADR-0011)
  - tagger.py     — source/section/topic теги (PR B)
  - embedder.py   — BGE-M3 эмбеддинги (PR C, ADR-0012)
  - store.py      — Chroma persist + upsert идемпотентно (PR C)
  - retriever.py  — bi-encoder retrieval + bge-reranker-v2-m3 (PR C)
  - schema.py     — pydantic схемы Document, Chunk, RetrievedChunk
"""
