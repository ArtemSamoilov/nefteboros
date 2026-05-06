#!/usr/bin/env python3
"""Полный chunking-pipeline: MD → чанки + теги → JSONL.

Этап 2 RAG-pipeline:
  1. Читает 25 MD из data/markdown/ (один на каждый source_id из manifest)
  2. Heading-aware chunking (см. nefteboros/rag/chunker.py)
  3. LLM-based topic-tagging через kimi-k2p6 (см. nefteboros/rag/tagger.py)
  4. Сохраняет в data/chunks/<source_id>.jsonl (по чанку на строку)

Идемпотентно: пропускает source_id, для которых JSONL уже существует.
Используй --force для перезаписи.

Usage:
    python scripts/chunk_corpus.py                           # все MD
    python scripts/chunk_corpus.py --only opec_woo,iea_oil   # подмножество
    python scripts/chunk_corpus.py --force                   # перезаписать
    python scripts/chunk_corpus.py --no-tag                  # только chunking, без LLM
    python scripts/chunk_corpus.py --model glm-5             # другая модель
    python scripts/chunk_corpus.py --concurrency 30          # больше параллели
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nefteboros.rag.chunker import iter_chunks_for_corpus  # noqa: E402
from nefteboros.rag.schema import Chunk  # noqa: E402

MANIFEST_PATH = ROOT / "data" / "metadata" / "manifest.yml"
MARKDOWN_DIR = ROOT / "data" / "markdown"
CHUNKS_DIR = ROOT / "data" / "chunks"


def _matches(sid: str, only: list[str] | None) -> bool:
    return not only or any(sub in sid for sub in only)


def _save_jsonl(path: Path, chunks: list[Chunk]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    with path.open("w", encoding="utf-8") as fh:
        for c in chunks:
            line = c.model_dump_json() + "\n"
            fh.write(line)
            bytes_written += len(line.encode("utf-8"))
    return bytes_written


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--only", help="Запятая-разделённый список подстрок source_id")
    p.add_argument("--force", action="store_true", help="Перезаписать существующие JSONL")
    p.add_argument(
        "--no-tag",
        action="store_true",
        help="Только chunking, без LLM-tagging (быстро, для отладки)",
    )
    p.add_argument(
        "--model",
        default="kimi-k2p6",
        help="LLM-модель для tagging через HydraGPT (default: kimi-k2p6)",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Параллельные LLM-запросы (default: 20)",
    )
    p.add_argument("--target-tokens", type=int, default=3000)
    p.add_argument("--max-tokens", type=int, default=4000)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("chunk_corpus")

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    documents = manifest.get("documents", [])

    # Фильтрация: что есть, что пропускаем
    todo_docs = []
    for doc in documents:
        sid = doc["id"]
        if not _matches(sid, only):
            continue
        md_path = MARKDOWN_DIR / f"{sid}.md"
        if not md_path.exists():
            log.warning("[skip no MD]    %s — нет %s (запусти convert_corpus.py)", sid, md_path)
            continue
        out_path = CHUNKS_DIR / f"{sid}.jsonl"
        if out_path.exists() and not args.force:
            log.info("[skip existing] %s — JSONL уже есть (--force чтобы перезаписать)", sid)
            continue
        todo_docs.append(doc)

    if not todo_docs:
        log.info("Нечего делать.")
        return 0

    log.info("В работу: %d документов", len(todo_docs))

    # Шаг 1 — chunking (быстро, локально)
    t0 = time.monotonic()
    all_chunks = list(iter_chunks_for_corpus(
        MARKDOWN_DIR,
        todo_docs,
        target_tokens=args.target_tokens,
        max_tokens=args.max_tokens,
    ))
    log.info(
        "Chunking готов: %d чанков, %.1f сек",
        len(all_chunks),
        time.monotonic() - t0,
    )

    # Шаг 2 — tagging (LLM)
    if not args.no_tag and all_chunks:
        from nefteboros.rag.tagger import tag_chunks_async

        log.info(
            "Tagging через %s (concurrency=%d) — может занять несколько минут...",
            args.model,
            args.concurrency,
        )
        t1 = time.monotonic()
        all_chunks = asyncio.run(
            tag_chunks_async(
                all_chunks, model=args.model, concurrency=args.concurrency
            )
        )
        log.info("Tagging готов: %.1f сек", time.monotonic() - t1)

    # Группируем по source и сохраняем
    by_source: dict[str, list[Chunk]] = defaultdict(list)
    for c in all_chunks:
        by_source[c.source_id].append(c)

    total_bytes = 0
    for sid, sc in by_source.items():
        out_path = CHUNKS_DIR / f"{sid}.jsonl"
        n_bytes = _save_jsonl(out_path, sc)
        total_bytes += n_bytes
        log.info("[ok] %-50s %d chunks, %d bytes", sid, len(sc), n_bytes)

    log.info(
        "Готово: %d чанков по %d документам, %.1f КБ JSONL",
        len(all_chunks),
        len(by_source),
        total_bytes / 1024,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
