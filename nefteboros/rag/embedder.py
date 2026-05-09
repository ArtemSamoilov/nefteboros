"""BGE-M3 эмбеддер для RAG (этап 3, см. ADR-0016).

Singleton-обёртка над `sentence-transformers.SentenceTransformer`. Модель
~2.3 ГБ грузится при первом обращении в `~/.cache/huggingface/`.

API:
    Embedder.get().embed(texts) -> list[list[float]]   # для документов
    Embedder.get().embed_query(text) -> list[float]    # для запросов
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Iterable

from nefteboros.observability._observe import observe

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("NEFTEBOROS_EMBED_MODEL", "BAAI/bge-m3")
# Эмпирика на M-серии MPS / CPU: при max_seq=4096 даже batch=4 даёт OOM на attention
# buffer (~16 GB). batch=1 + max_seq=4096 укладывается в ~1 GB. На CUDA-GPU 8 GB+
# можно поднимать до 8-16 через env.
DEFAULT_BATCH_SIZE = int(os.environ.get("NEFTEBOROS_EMBED_BATCH", "1"))
# Наш max chunk = 4031 токенов (см. ADR-0011). Ограничиваем native 8192 BGE-M3 → 4096
# — экономим память attention'а вдвое, никаких чанков не truncate'ит.
DEFAULT_MAX_SEQ_LEN = int(os.environ.get("NEFTEBOROS_EMBED_MAX_SEQ", "4096"))
DEFAULT_DIM = 1024  # BGE-M3 dim


class Embedder:
    """Singleton wrapper над sentence-transformers SentenceTransformer."""

    _instance: "Embedder | None" = None
    _lock = threading.Lock()

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        max_seq_length: int = DEFAULT_MAX_SEQ_LEN,
    ):
        from sentence_transformers import SentenceTransformer

        logger.info(
            "Loading embedder model %s (max_seq_length=%d, ~2.3 GB on first run)",
            model_name,
            max_seq_length,
        )
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        # Ограничиваем seq_length, иначе attention взрывается на длинных чанках
        self.model.max_seq_length = max_seq_length
        self.dim = DEFAULT_DIM

    @classmethod
    def get(cls, model_name: str = DEFAULT_MODEL) -> "Embedder":
        """Возвращает singleton, ленивая загрузка."""
        with cls._lock:
            if cls._instance is None or cls._instance.model_name != model_name:
                cls._instance = cls(model_name)
            return cls._instance

    def embed(
        self,
        texts: list[str] | Iterable[str],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        normalize: bool = True,
        show_progress: bool = False,
    ) -> list[list[float]]:
        """Эмбеддит коллекцию документов. Нормализованные вектора → cosine = dot."""
        texts = list(texts)
        if not texts:
            return []
        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=normalize,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
        )
        return embeddings.tolist()

    @observe(name="embed_query", as_type="embedding")
    def embed_query(self, text: str, *, normalize: bool = True) -> list[float]:
        """Эмбеддит один запрос. Виден в Langfuse как `embedding` observation."""
        return self.embed([text], normalize=normalize)[0]
