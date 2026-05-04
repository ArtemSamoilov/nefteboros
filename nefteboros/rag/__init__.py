"""RAG-пайплайн: PDF → чанки с метаданными → BGE-M3 → ChromaDB → retriever.

Будет содержать:
  - parser.py     — PyMuPDF парсер с извлечением layout и метаданных
  - chunker.py    — семантическая разбивка с overlap
  - indexer.py    — embedding + upsert в ChromaDB (идемпотентный)
  - retriever.py  — top-k поиск с метаданными и score
  - schema.py     — pydantic схемы Document, Chunk, RetrievedChunk

См. docs/adr/0003-chromadb-choice.md (TBD), docs/adr/0002-bge-m3-embeddings.md (TBD).
"""
