#!/usr/bin/env python3
"""Сборка vector store из data/chunks/*.jsonl.

Этап 3 RAG-pipeline:
  1. Читает 25 JSONL-файлов из data/chunks/
  2. Эмбеддит chunk.text через BGE-M3 (~2-3 мин на CPU для 802 чанков)
  3. Upsert в ChromaDB (data/vectorstore/) с метаданными из chroma_metadata()

Идемпотентно: повторный запуск пропускает уже проиндексированные chunk_id.

Usage:
    python scripts/build_index.py                   # дельта
    python scripts/build_index.py --force           # сбросить коллекцию и пересобрать
    python scripts/build_index.py --only opec_woo   # только подмножество
    python scripts/build_index.py --batch-size 32   # больше batch для embedder
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nefteboros.rag.embedder import DEFAULT_BATCH_SIZE, Embedder  # noqa: E402
from nefteboros.rag.schema import Chunk  # noqa: E402
from nefteboros.rag.store import VectorStore  # noqa: E402

CHUNKS_DIR = ROOT / "data" / "chunks"


def _load_chunks(only: list[str] | None = None) -> list[Chunk]:
    files = sorted(CHUNKS_DIR.glob("*.jsonl"))
    chunks: list[Chunk] = []
    for f in files:
        sid = f.stem
        if only and not any(sub in sid for sub in only):
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunks.append(Chunk.model_validate_json(line))
    return chunks


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--only", help="Запятая-разделённый список подстрок source_id")
    p.add_argument("--force", action="store_true", help="Сбросить коллекцию и пересобрать с нуля")
    p.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Embedder batch size (default: NEFTEBOROS_EMBED_BATCH или 1; "
        "поднимай только при наличии CUDA GPU 8+ ГБ)",
    )
    p.add_argument(
        "--with-heading-prefix",
        action="store_true",
        help="Эксперимент: добавить [source_title] + section_path в text перед embedding (см. ADR-0016 / experiments)",
    )
    p.add_argument(
        "--collection",
        default=None,
        help="Имя collection (default: NEFTEBOROS_RAG_COLLECTION или nefteboros_corpus_v1). "
        "Используй другое для A/B экспериментов",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("build_index")

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    chunks = _load_chunks(only=only)

    if not chunks:
        log.warning("Нет чанков в %s — запусти scripts/chunk_corpus.py", CHUNKS_DIR)
        return 1

    log.info("Загружено %d чанков из %d source-файлов", len(chunks), len({c.source_id for c in chunks}))
    if args.with_heading_prefix:
        log.info("Embedding mode: text_for_embedding(with_heading_prefix=True) — эксперимент")

    store = VectorStore.open(collection_name=args.collection) if args.collection else VectorStore.open()

    if args.force:
        log.info("--force: drop коллекции %s", store._collection_name)
        store.reset()

    existing = store.existing_ids() if not args.force else set()
    log.info("В коллекции уже %d чанков", len(existing))

    todo = [c for c in chunks if c.id not in existing]
    log.info("Будем эмбеддить и апсёртить: %d новых чанков", len(todo))

    if not todo:
        log.info("Всё уже на месте.")
        # Удалим orphans (если manifest сжали — chunks из старых документов уйдут)
        target_ids = {c.id for c in chunks}
        orphans = [eid for eid in existing if eid not in target_ids]
        if orphans:
            log.info("Удаляю %d orphans", len(orphans))
            store.delete(orphans)
        return 0

    log.info("Загружаю BGE-M3 (~2.3 ГБ при первом запуске)...")
    t_load = time.monotonic()
    embedder = Embedder.get()
    log.info("Embedder готов за %.1f сек", time.monotonic() - t_load)

    batch_size = args.batch_size if args.batch_size is not None else DEFAULT_BATCH_SIZE
    log.info("Эмбеддинг %d чанков (batch_size=%d)...", len(todo), batch_size)
    t_embed = time.monotonic()
    # text_for_embedding() возвращает либо c.text, либо c.text с heading-prefix
    embed_texts = [c.text_for_embedding(with_heading_prefix=args.with_heading_prefix) for c in todo]
    # В Chroma documents хранится оригинальный c.text — чтобы retrieval возвращал
    # пользователю контент без префикса в начале
    docs_for_store = [c.text for c in todo]
    embeddings = embedder.embed(embed_texts, batch_size=batch_size, show_progress=True)
    log.info(
        "Embeddings готовы за %.1f сек (%.1f чанков/сек)",
        time.monotonic() - t_embed,
        len(todo) / max(time.monotonic() - t_embed, 1e-9),
    )

    log.info("Upsert в Chroma...")
    t_upsert = time.monotonic()
    store.upsert(
        ids=[c.id for c in todo],
        documents=docs_for_store,
        embeddings=embeddings,
        metadatas=[c.chroma_metadata() for c in todo],
    )
    log.info("Upsert готов за %.1f сек", time.monotonic() - t_upsert)

    # Distribution по источникам
    by_source: dict[str, int] = defaultdict(int)
    for c in todo:
        by_source[c.source_id] += 1
    log.info("Проиндексировано по источникам:")
    for sid in sorted(by_source):
        log.info("  %-50s %d", sid, by_source[sid])

    log.info("Total в коллекции: %d", store.count())
    return 0


if __name__ == "__main__":
    sys.exit(main())
