"""ChromaDB persistent vector store для RAG (этап 3, см. ADR-0016).

Один collection на весь корпус. Чанки апсёртятся по `chunk.id` —
идемпотентно при повторных запусках.

API:
    store = VectorStore.open()
    store.upsert(chunks, embeddings)
    results = store.search(query_embedding, k=30, where={"language": "ru"})
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(
    os.environ.get(
        "NEFTEBOROS_RAG_VECTORSTORE_PATH",
        str(Path(__file__).resolve().parent.parent.parent / "data" / "vectorstore"),
    )
)
DEFAULT_COLLECTION = os.environ.get("NEFTEBOROS_RAG_COLLECTION", "nefteboros_corpus_v2_heading")


@dataclass
class SearchHit:
    """Один результат retrieval."""

    chunk_id: str
    score: float  # cosine similarity (1.0 — идеал)
    text: str
    metadata: dict


class VectorStore:
    """Tonkaya обёртка над chromadb PersistentClient."""

    def __init__(self, path: Path, collection_name: str):
        import chromadb

        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection_name = collection_name
        # cosine — нормализованные эмбеддинги BGE-M3
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self.path = path

    @classmethod
    def open(
        cls, path: Path = DEFAULT_PATH, collection_name: str = DEFAULT_COLLECTION
    ) -> "VectorStore":
        return cls(path, collection_name)

    def count(self) -> int:
        return self._collection.count()

    def existing_ids(self) -> set[str]:
        """Возвращает все ID, уже присутствующие в коллекции."""
        # ChromaDB поддерживает get(ids=None) → все
        return set(self._collection.get(include=[])["ids"])

    def upsert(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        """Идемпотентный upsert — chunks с теми же ID перезапишутся."""
        if not ids:
            return
        self._collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._collection.delete(ids=ids)

    def reset(self) -> None:
        """Удаляет коллекцию целиком и пересоздаёт пустую."""
        self._client.delete_collection(self._collection_name)
        import chromadb  # noqa: F401  # ensure imported
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def search(
        self,
        query_embedding: list[float],
        *,
        k: int = 30,
        where: dict | None = None,
    ) -> list[SearchHit]:
        """Top-k cosine search с опциональным metadata-фильтром."""
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        # Chroma возвращает batched (один query — берём [0])
        ids = result["ids"][0]
        docs = result["documents"][0]
        metas = result["metadatas"][0]
        dists = result["distances"][0]
        # cosine distance = 1 - similarity → конвертируем
        return [
            SearchHit(
                chunk_id=cid,
                score=1.0 - d,
                text=doc,
                metadata=meta,
            )
            for cid, doc, meta, d in zip(ids, docs, metas, dists)
        ]
