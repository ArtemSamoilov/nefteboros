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
from nefteboros.rag.query_classifier import QueryClassifier, topic_overlap_score
from nefteboros.rag.schema import TopicTags
from nefteboros.rag.store import SearchHit, VectorStore

logger = logging.getLogger(__name__)

DEFAULT_RERANKER = os.environ.get(
    "NEFTEBOROS_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"
)
DEFAULT_K_DENSE = int(os.environ.get("NEFTEBOROS_RETRIEVAL_K_DENSE", "30"))
DEFAULT_K_FINAL = int(os.environ.get("NEFTEBOROS_RETRIEVAL_K_FINAL", "5"))
# По умолчанию reranker ОТКЛЮЧЁН — bge-reranker-v2-m3 (~2.3 ГБ) не вмещается
# на сервере 4 ГБ рядом с BGE-M3 embedder'ом (~1 ГБ) и Ouroboros core.
# Reranker доступен через rerank=True для off-server eval / dev.
# См. ADR-0016, секция «Calibration on CPU».
DEFAULT_RERANK = os.environ.get("NEFTEBOROS_RETRIEVAL_RERANK", "false").lower() == "true"

# Topic/type-filter режимы (см. docs/experiments/rag-prefix-experiments.md):
#   "off"            — не классифицируем query, retrieval по embedding only (default)
#   "boost"          — soft: bonus к score за каждый matching topic-tag
#   "filter"         — strict topic-tag post-filter с fallback
#   "doc-type"       — Chroma `where={"type": {"$in": [...]}}` через query → doc_types LLM
#                       (strict, на synthetic dataset даёт регресс — см. эксперимент)
#   "doc-type-boost" — soft: bonus за совпадение type без отрезания chunks
# Каждый режим стоит +1 LLM call на запрос (~2-5 сек latency).
DEFAULT_TOPIC_FILTER = os.environ.get("NEFTEBOROS_TOPIC_FILTER", "off").lower()
# Бонус за один tag overlap (для режима boost). Bi-encoder cosine ∈ [0, 1],
# 0.05 за tag даёт максимум +0.5 (до 10 совпадений) — сравнимо с базовой
# вариацией score между близкими chunks.
TOPIC_BOOST_PER_MATCH = float(os.environ.get("NEFTEBOROS_TOPIC_BOOST", "0.05"))
# Doc-type boost: один бинарный bonus за match (chunk type ∈ predicted query types).
# Должен быть выше topic boost — type — более specific сигнал.
DOC_TYPE_BOOST = float(os.environ.get("NEFTEBOROS_DOC_TYPE_BOOST", "0.10"))


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
    """Высокоуровневый: embed query → search store → (topic-filter) → rerank → return top-k."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        query_classifier: QueryClassifier | None = None,
    ):
        self._store = store
        self._embedder = embedder
        self._reranker = reranker  # ленивая загрузка через .get() при первом retrieve
        self._classifier = query_classifier  # lazy

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

    @property
    def classifier(self) -> QueryClassifier:
        if self._classifier is None:
            self._classifier = QueryClassifier.get()
        return self._classifier

    def _apply_topic_boost(
        self, candidates: list[SearchHit], query_tags: TopicTags, top_k: int
    ) -> list[SearchHit]:
        """Boost: новый score = bi_encoder_score + bonus * topic_overlap."""
        boosted = []
        for c in candidates:
            overlap = topic_overlap_score(query_tags, c.metadata)
            new_score = c.score + TOPIC_BOOST_PER_MATCH * overlap
            boosted.append(SearchHit(
                chunk_id=c.chunk_id,
                score=new_score,
                text=c.text,
                metadata=c.metadata,
            ))
        boosted.sort(key=lambda h: -h.score)
        return boosted[:top_k]

    def _apply_doc_type_boost(
        self, candidates: list[SearchHit], doc_types: list[str], top_k: int
    ) -> list[SearchHit]:
        """Doc-type boost: бинарный bonus за совпадение chunk type с query types.
        Soft — не отрезает chunks с другим типом, только переранжирует.
        """
        if not doc_types:
            return candidates[:top_k]
        type_set = set(doc_types)
        boosted = []
        for c in candidates:
            chunk_type = c.metadata.get("type", "")
            new_score = c.score + (DOC_TYPE_BOOST if chunk_type in type_set else 0.0)
            boosted.append(SearchHit(
                chunk_id=c.chunk_id,
                score=new_score,
                text=c.text,
                metadata=c.metadata,
            ))
        boosted.sort(key=lambda h: -h.score)
        return boosted[:top_k]

    def _apply_topic_filter(
        self, candidates: list[SearchHit], query_tags: TopicTags, top_k: int
    ) -> list[SearchHit]:
        """Strict filter: оставляем только chunks с ≥1 matching tag.
        Fallback к unfiltered если получилось меньше top_k.
        """
        filtered = [
            c for c in candidates
            if topic_overlap_score(query_tags, c.metadata) > 0
        ]
        if len(filtered) >= top_k:
            return filtered[:top_k]
        # fallback: дополняем unfiltered'ами, сохраняя порядок
        seen_ids = {c.chunk_id for c in filtered}
        for c in candidates:
            if c.chunk_id not in seen_ids:
                filtered.append(c)
                if len(filtered) >= top_k:
                    break
        return filtered[:top_k]

    def retrieve(
        self,
        query: str,
        *,
        k_dense: int = DEFAULT_K_DENSE,
        k_final: int = DEFAULT_K_FINAL,
        where: dict | None = None,
        rerank: bool = DEFAULT_RERANK,
        topic_filter: str = DEFAULT_TOPIC_FILTER,
    ) -> list[RankedHit]:
        """Pipeline: embed query → top-k_dense из Chroma → topic-filter (опц.) → rerank → top-k_final.

        topic_filter:
            "off"    — не классифицируем query (default, нет +LLM latency)
            "boost"  — добавляем bonus к score за topic overlap
            "filter" — strict filter с fallback
        """
        q_emb = self.embedder.embed_query(query)

        # doc-type режим: Chroma where filter ДО retrieval (server-side narrow)
        if topic_filter == "doc-type":
            doc_types = self.classifier.classify_doc_types(query)
            if doc_types:
                # Merge с существующим where, не перетираем
                type_filter = {"type": {"$in": doc_types}}
                where = {"$and": [where, type_filter]} if where else type_filter
            # Если LLM вернул пусто — идём без фильтра (graceful degradation)

        # Если post-filter включён, ретривим больше кандидатов для запаса
        pool_size = k_dense * 2 if topic_filter in ("boost", "filter", "doc-type-boost") else k_dense
        candidates = self.store.search(q_emb, k=pool_size, where=where)

        # boost / filter / doc-type-boost — post-retrieval, требуют LLM call
        if topic_filter in ("boost", "filter") and candidates:
            query_tags = self.classifier.classify(query)
            if topic_filter == "boost":
                candidates = self._apply_topic_boost(candidates, query_tags, top_k=k_dense)
            elif topic_filter == "filter":
                candidates = self._apply_topic_filter(candidates, query_tags, top_k=k_dense)
        elif topic_filter == "doc-type-boost" and candidates:
            doc_types = self.classifier.classify_doc_types(query)
            candidates = self._apply_doc_type_boost(candidates, doc_types, top_k=k_dense)

        if not rerank or not candidates:
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
