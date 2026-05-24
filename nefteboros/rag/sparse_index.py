"""BM25 sparse-индекс над корпусом чанков (см. ADR-0027).

Дополняет dense BGE-M3 retrieval лексическим матчем по точным термам (тикеры,
имена компаний, числа) — это бьёт по failure-mode SAME_DOC_MISS, где нужный
source найден, но внутри него выше ранжируется не тот чанк.

In-memory BM25Okapi над 802 чанками (~13 МБ, без второй модели) — server-safe,
в отличие от cross-encoder reranker'а, отключённого из-за 4 ГБ RAM (ADR-0016).
Токенизированный корпус кэшируется на диск, чтобы лемматизация pymorphy3
прогонялась один раз, а не на каждый старт процесса.

API:
    idx = SparseIndex.get()
    hits = idx.search("price cap на российскую нефть", k=30)
    for h in hits:
        print(f"{h.score:.3f} | {h.metadata['source_id']} | {h.text[:80]}")
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from nefteboros.observability._observe import observe
from nefteboros.rag.text_norm import TOKENIZER_VERSION, tokenize

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CHUNKS_DIR = Path(
    os.environ.get("NEFTEBOROS_CHUNKS_DIR", str(_ROOT / "data" / "chunks"))
)
DEFAULT_CACHE_DIR = Path(
    os.environ.get("NEFTEBOROS_SPARSE_CACHE_DIR", str(_ROOT / "data" / "sparse_index"))
)

# Поля метаданных, которые тащим в hit (зеркалит chroma_metadata в объёме,
# нужном для eval/failure-analysis: source_id, language, type, section_path...).
_META_FIELDS = (
    "source_id",
    "source_title",
    "language",
    "block",
    "type",
    "section_path",
    "page_start",
    "page_end",
    "has_table",
    "is_table_only",
    "chunk_idx",
)


@dataclass
class SparseHit:
    """Один результат BM25-retrieval. Совместим по полям с store.SearchHit."""

    chunk_id: str
    score: float  # BM25-скор (unbounded, зависит от корпуса)
    text: str
    metadata: dict


class SparseIndex:
    """Singleton-обёртка над BM25Okapi с дисковым кэшем токенизации."""

    _instance: "SparseIndex | None" = None
    _lock = threading.Lock()

    def __init__(
        self,
        chunks_dir: Path = DEFAULT_CHUNKS_DIR,
        cache_dir: Path = DEFAULT_CACHE_DIR,
    ):
        self.chunks_dir = chunks_dir
        self.cache_dir = cache_dir
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._metas: list[dict] = []
        self._pos: dict[str, int] = {}  # chunk_id -> индекс в массивах

        self._load_chunks()
        tokenized = self._tokenized_corpus()

        from rank_bm25 import BM25Okapi

        logger.info("Строю BM25Okapi над %d чанками", len(tokenized))
        self._bm25 = BM25Okapi(tokenized)

    @classmethod
    def get(cls) -> "SparseIndex":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __len__(self) -> int:
        return len(self._ids)

    def _load_chunks(self) -> None:
        """Читает data/chunks/*.jsonl. Сортировка по id — детерминизм кэша/BM25."""
        files = sorted(self.chunks_dir.glob("*.jsonl"))
        rows: list[dict] = []
        for f in files:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        rows.sort(key=lambda c: c["id"])
        for c in rows:
            self._ids.append(c["id"])
            self._texts.append(c["text"])
            self._metas.append({k: c.get(k) for k in _META_FIELDS})
        self._pos = {cid: i for i, cid in enumerate(self._ids)}
        logger.info("SparseIndex: загружено %d чанков из %s", len(self._ids), self.chunks_dir)

    def _corpus_signature(self) -> str:
        """Хэш по (версия токенайзера + id:len каждого чанка). Меняется при
        правке корпуса или логики токенизации → инвалидирует кэш."""
        h = hashlib.sha1()
        h.update(TOKENIZER_VERSION.encode())
        for cid, text in zip(self._ids, self._texts):
            h.update(f"\n{cid}:{len(text)}".encode())
        return h.hexdigest()

    def _tokenized_corpus(self) -> list[list[str]]:
        """Токенизированный корпус: из дискового кэша или свежий прогон."""
        signature = self._corpus_signature()
        cache_path = self.cache_dir / "sparse_tokens.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("signature") == signature and cached.get("ids") == self._ids:
                    logger.info("SparseIndex: токены из кэша %s", cache_path)
                    return cached["tokens"]
                logger.info("SparseIndex: кэш устарел (signature mismatch) — пересборка")
            except Exception as exc:  # noqa: BLE001 — битый кэш не должен ронять старт
                logger.warning("SparseIndex: не смог прочитать кэш (%s) — пересборка", exc)

        logger.info("SparseIndex: токенизирую %d чанков (pymorphy3)...", len(self._texts))
        tokenized = [tokenize(t) for t in self._texts]
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {"signature": signature, "ids": self._ids, "tokens": tokenized},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            logger.info("SparseIndex: кэш токенов записан в %s", cache_path)
        except Exception as exc:  # noqa: BLE001 — кэш опционален
            logger.warning("SparseIndex: не смог записать кэш (%s)", exc)
        return tokenized

    @observe(name="sparse_search", as_type="retriever")
    def search(self, query: str, *, k: int = 30) -> list[SparseHit]:
        """Top-k чанков по BM25. Виден в Langfuse как `retriever` observation.

        Возвращает только чанки со score > 0 (есть лексическое пересечение) —
        нулевой хвост не несёт сигнала и только зашумил бы fusion по рангам.
        """
        q_tokens = tokenize(query)
        if not q_tokens or not self._ids:
            return []
        scores = self._bm25.get_scores(q_tokens)
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
        hits: list[SparseHit] = []
        for i in ranked[:k]:
            if scores[i] <= 0:
                break
            hits.append(
                SparseHit(
                    chunk_id=self._ids[i],
                    score=float(scores[i]),
                    text=self._texts[i],
                    metadata=self._metas[i],
                )
            )
        return hits

    def record(self, chunk_id: str) -> SparseHit | None:
        """Текст+метаданные чанка по id (для подтягивания sparse-only хитов
        в fusion, когда dense их не вернул). score=0.0 — заглушка."""
        i = self._pos.get(chunk_id)
        if i is None:
            return None
        return SparseHit(
            chunk_id=chunk_id, score=0.0, text=self._texts[i], metadata=self._metas[i]
        )
