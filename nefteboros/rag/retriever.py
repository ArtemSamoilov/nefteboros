"""Retriever — bi-encoder + cross-encoder reranker (этап 3, см. ADR-0016).

Двухэтапная схема:
  1. BGE-M3 retrieval по Chroma → top-k_dense (default 30)
  2. bge-reranker-v2-m3 cross-encoder → top-k_final (default 5)

API:
    retriever = Retriever.get()
    hits = retriever.retrieve("Что говорит ОПЕК+ про квоты?", k_final=5)
    for h in hits:
        print(f"{h.score:.3f} | {h.metadata['source_id']} | {h.text[:100]}")
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

from nefteboros.rag.embedder import Embedder
from nefteboros.rag.store import SearchHit, VectorStore

logger = logging.getLogger(__name__)

DEFAULT_RERANKER = os.environ.get(
    "NEFTEBOROS_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"
)
DEFAULT_K_DENSE = int(os.environ.get("NEFTEBOROS_RETRIEVAL_K_DENSE", "30"))
DEFAULT_K_FINAL = int(os.environ.get("NEFTEBOROS_RETRIEVAL_K_FINAL", "5"))


@dataclass
class RankedHit:
    """Hit после reranker'а."""

    chunk_id: str
    bi_encoder_score: float  # cosine sim
    rerank_score: float  # logit от cross-encoder
    text: str
    metadata: dict


class Reranker:
    """Singleton-wrapper над cross-encoder."""

    _instance: "Reranker | None" = None
    _lock = threading.Lock()

    def __init__(self, model_name: str = DEFAULT_RERANKER):
        from sentence_transformers import CrossEncoder

        logger.info("Loading reranker model %s (~2.3 GB on first run)", model_name)
        self.model_name = model_name
        self.model = CrossEncoder(model_name)

    @classmethod
    def get(cls, model_name: str = DEFAULT_RERANKER) -> "Reranker":
        with cls._lock:
            if cls._instance is None or cls._instance.model_name != model_name:
                cls._instance = cls(model_name)
            return cls._instance

    def rerank(
        self, query: str, candidates: list[SearchHit], top_k: int
    ) -> list[RankedHit]:
        if not candidates:
            return []
        pairs = [(query, c.text) for c in candidates]
        scores = self.model.predict(pairs, convert_to_numpy=True).tolist()
        ranked = [
            RankedHit(
                chunk_id=c.chunk_id,
                bi_encoder_score=c.score,
                rerank_score=float(s),
                text=c.text,
                metadata=c.metadata,
            )
            for c, s in zip(candidates, scores)
        ]
        ranked.sort(key=lambda h: -h.rerank_score)
        return ranked[:top_k]


class Retriever:
    """Высокоуровневый: embed query → search store → rerank → return top-k."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ):
        self._store = store
        self._embedder = embedder
        self._reranker = reranker  # ленивая загрузка через .get() при первом retrieve

    @classmethod
    def get(cls) -> "Retriever":
        return cls()

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = VectorStore.open()
        return self._store

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder.get()
        return self._embedder

    @property
    def reranker(self) -> Reranker:
        if self._reranker is None:
            self._reranker = Reranker.get()
        return self._reranker

    def retrieve(
        self,
        query: str,
        *,
        k_dense: int = DEFAULT_K_DENSE,
        k_final: int = DEFAULT_K_FINAL,
        where: dict | None = None,
        rerank: bool = True,
    ) -> list[RankedHit]:
        """Pipeline: embed query → top-k_dense из Chroma → rerank → top-k_final."""
        q_emb = self.embedder.embed_query(query)
        candidates = self.store.search(q_emb, k=k_dense, where=where)
        if not rerank or not candidates:
            # без reranker'а возвращаем bi-encoder скоры как rerank_score
            return [
                RankedHit(
                    chunk_id=c.chunk_id,
                    bi_encoder_score=c.score,
                    rerank_score=c.score,
                    text=c.text,
                    metadata=c.metadata,
                )
                for c in candidates[:k_final]
            ]
        return self.reranker.rerank(query, candidates, top_k=k_final)
