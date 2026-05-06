#!/usr/bin/env python3
"""Failure analysis для RAG retriever — почему chunk_hit@5 = 65%?

Для каждого вопроса в датасете делает retrieval и классифицирует результат:

  CHUNK_HIT          — правильный chunk_id в top-5 ✓
  SAME_DOC_MISS      — правильного chunk_id нет в top-5, но source_id есть
                       (similar chunks внутри одного источника конкурируют)
  CROSS_DOC_MISS     — правильного source_id нет в top-5
                       (embedding completely off — серьёзная ошибка)

Дополнительно ищет паттерны:
  - by content type (table_only / text)
  - by chunk_size (короткий / средний / длинный)
  - by source-уровню (какие источники чаще промахиваются)

Ходит в Chroma локально, использует тот же Retriever что и production.

Usage:
    python scripts/eval/analyze_rag_failures.py --version v1
    python scripts/eval/analyze_rag_failures.py --version v1 --dump-misses /tmp/misses.tsv
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nefteboros.rag.retriever import Retriever  # noqa: E402

DATASETS_DIR = ROOT / "datasets" / "rag_eval"
CHUNKS_DIR = ROOT / "data" / "chunks"


def load_chunks_index() -> dict[str, dict]:
    """{chunk_id: chunk_dict_full}."""
    out = {}
    for f in CHUNKS_DIR.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                out[c["id"]] = c
    return out


def classify(target_chunk_id: str, target_source_id: str, hits: list) -> str:
    chunk_ids_top5 = [h.chunk_id for h in hits[:5]]
    source_ids_top5 = [h.metadata.get("source_id", "") for h in hits[:5]]
    if target_chunk_id in chunk_ids_top5:
        return "CHUNK_HIT"
    if target_source_id in source_ids_top5:
        return "SAME_DOC_MISS"
    return "CROSS_DOC_MISS"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", default="v1")
    p.add_argument("--k-dense", type=int, default=30)
    p.add_argument("--k-final", type=int, default=10)
    p.add_argument("--dump-misses", help="TSV-файл с детальной информацией по промахам")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("analyze")

    dataset = [json.loads(l) for l in (DATASETS_DIR / f"{args.version}.jsonl").read_text().splitlines() if l.strip()]
    chunks_idx = load_chunks_index()
    log.info("Loaded %d Q + %d chunks", len(dataset), len(chunks_idx))

    retriever = Retriever()
    log.info("Running retrieval on %d questions...", len(dataset))

    classified: list[dict] = []
    for i, item in enumerate(dataset, 1):
        target_chunk_id = item["expected_chunk_id"]
        target_source_id = item["expected_source_id"]
        hits = retriever.retrieve(item["question"], k_dense=args.k_dense, k_final=args.k_final, rerank=False)
        category = classify(target_chunk_id, target_source_id, hits)

        target = chunks_idx.get(target_chunk_id, {})
        classified.append({
            "id": item["id"],
            "question": item["question"],
            "expected_chunk_id": target_chunk_id,
            "expected_source_id": target_source_id,
            "category": category,
            "language": item["language"],
            "block": item["block"],
            "target_token_count": target.get("token_count", 0),
            "target_is_table_only": target.get("is_table_only", False),
            "target_has_table": target.get("has_table", False),
            "top5_chunk_ids": [h.chunk_id for h in hits[:5]],
            "top5_source_ids": [h.metadata.get("source_id", "") for h in hits[:5]],
            "top5_scores": [round(h.bi_encoder_score, 3) for h in hits[:5]],
        })
        if i % 20 == 0 or i == len(dataset):
            log.info("  processed %d/%d", i, len(dataset))

    # Aggregate
    cats = Counter(r["category"] for r in classified)
    n = len(classified)
    print(f"\n{'='*72}\nFAILURE BREAKDOWN — {n} questions\n{'='*72}")
    for cat in ("CHUNK_HIT", "SAME_DOC_MISS", "CROSS_DOC_MISS"):
        count = cats.get(cat, 0)
        bar = "█" * (count * 50 // n)
        print(f"  {cat:18s} {count:3d}  {100*count/n:5.1f}%  {bar}")

    # By table_only
    print(f"\n{'='*72}\nBY CONTENT TYPE\n{'='*72}")
    table_stats = defaultdict(lambda: Counter())
    for r in classified:
        key = "table_only" if r["target_is_table_only"] else ("with_table" if r["target_has_table"] else "text_only")
        table_stats[key][r["category"]] += 1
    for kind in ("text_only", "with_table", "table_only"):
        c = table_stats[kind]
        total = sum(c.values())
        if total == 0:
            continue
        hit_pct = 100 * c.get("CHUNK_HIT", 0) / total
        print(f"  {kind:15s} (n={total:3d}): chunk_hit@5={hit_pct:.1f}%  | {dict(c)}")

    # By token_count bucket
    print(f"\n{'='*72}\nBY CHUNK SIZE\n{'='*72}")
    def bucket(n):
        if n < 1000: return "<1000"
        if n < 2500: return "1000-2500"
        if n < 3500: return "2500-3500"
        return "3500+"
    size_stats = defaultdict(lambda: Counter())
    for r in classified:
        size_stats[bucket(r["target_token_count"])][r["category"]] += 1
    for kind in ("<1000", "1000-2500", "2500-3500", "3500+"):
        c = size_stats[kind]
        total = sum(c.values())
        if total == 0:
            continue
        hit_pct = 100 * c.get("CHUNK_HIT", 0) / total
        print(f"  {kind:12s} (n={total:3d}): chunk_hit@5={hit_pct:.1f}%  | {dict(c)}")

    # By source_id (топ-10 худших)
    print(f"\n{'='*72}\nWORST SOURCES (chunk_miss rate)\n{'='*72}")
    src_stats = defaultdict(lambda: Counter())
    for r in classified:
        src_stats[r["expected_source_id"]][r["category"]] += 1
    src_miss = []
    for sid, c in src_stats.items():
        total = sum(c.values())
        miss = (c.get("SAME_DOC_MISS", 0) + c.get("CROSS_DOC_MISS", 0))
        src_miss.append((sid, total, miss, miss/total))
    src_miss.sort(key=lambda x: -x[3])
    print(f"  {'source_id':40s} {'n':>3s} {'miss':>5s}  rate")
    for sid, total, miss, rate in src_miss[:10]:
        print(f"  {sid:40s} {total:>3d} {miss:>5d}  {100*rate:5.1f}%")

    # Cross-doc misses — самые серьёзные ошибки
    print(f"\n{'='*72}\nCROSS_DOC_MISS examples (top-5)\n{'='*72}")
    cross_misses = [r for r in classified if r["category"] == "CROSS_DOC_MISS"]
    for r in cross_misses[:5]:
        print(f"\n  Q: {r['question'][:100]}")
        print(f"    expected source: {r['expected_source_id']}")
        print(f"    got top-5 sources: {list(dict.fromkeys(r['top5_source_ids']))}")

    # Dump misses if requested
    if args.dump_misses:
        out_path = Path(args.dump_misses)
        with out_path.open("w", encoding="utf-8") as f:
            f.write("category\tlanguage\tblock\texpected_source_id\texpected_chunk_id\tquestion\ttop5_sources\n")
            for r in classified:
                if r["category"] != "CHUNK_HIT":
                    f.write("\t".join([
                        r["category"], r["language"], r["block"],
                        r["expected_source_id"], r["expected_chunk_id"],
                        r["question"].replace("\t", " "),
                        ",".join(r["top5_source_ids"]),
                    ]) + "\n")
        log.info("Dumped misses to %s", out_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
