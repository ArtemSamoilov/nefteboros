#!/usr/bin/env python3
"""Eval RAG retriever на голден-датасете datasets/rag_eval/<version>.jsonl.

Метрики:
  hit@k (k=1,3,5,10) — доля вопросов где правильный chunk_id попал в top-k
  source_hit@k       — то же, но матчим по source_id (loose match — у источника много чанков)
  MRR                — Mean Reciprocal Rank (по chunk_id)
  source_MRR         — то же по source_id

Слайсы: по language (ru/en) и block (1_strategy / 2_corporate / 3_operational / 4_geopolitics).

Конфигурации (--config):
  bi          — только bi-encoder retrieval (default, server-friendly)
  bi+rerank   — bi-encoder + bge-reranker-v2-m3 (off-server, нужно много RAM/GPU)

Usage:
    python scripts/eval/eval_rag.py --version v1                 # bi-encoder
    python scripts/eval/eval_rag.py --version v1 --config bi+rerank
    python scripts/eval/eval_rag.py --version v1 --k-dense 50
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nefteboros.rag.retriever import Retriever  # noqa: E402

DATASETS_DIR = ROOT / "datasets" / "rag_eval"
RUNS_DIR = ROOT / "metrics" / "runs"

K_VALUES = (1, 3, 5, 10)


def load_dataset(version: str) -> list[dict]:
    path = DATASETS_DIR / f"{version}.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            f"Eval dataset not found: {path}. Run build_rag_eval_dataset.py first."
        )
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reciprocal_rank(hits_ids: list[str], target: str) -> float:
    for i, hid in enumerate(hits_ids, start=1):
        if hid == target:
            return 1.0 / i
    return 0.0


def hit_at_k(hits_ids: list[str], target: str, k: int) -> int:
    return int(target in hits_ids[:k])


def get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", default="v1")
    p.add_argument("--config", default="bi", choices=["bi", "bi+rerank"])
    p.add_argument("--k-dense", type=int, default=30)
    p.add_argument("--k-final", type=int, default=10)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", help="Путь для метрик (default: metrics/runs/...)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("eval_rag")

    dataset = load_dataset(args.version)
    if args.limit:
        dataset = dataset[: args.limit]
    log.info("Загружен датасет %s — %d вопросов", args.version, len(dataset))

    retriever = Retriever()
    rerank = args.config == "bi+rerank"

    log.info("Конфиг: %s | k_dense=%d k_final=%d", args.config, args.k_dense, args.k_final)

    per_question_results: list[dict] = []

    for i, item in enumerate(dataset, start=1):
        target_chunk_id = item["expected_chunk_id"]
        target_source_id = item["expected_source_id"]

        hits = retriever.retrieve(
            item["question"],
            k_dense=args.k_dense,
            k_final=args.k_final,
            rerank=rerank,
        )
        chunk_ids = [h.chunk_id for h in hits]
        source_ids = [h.metadata.get("source_id", "") for h in hits]

        per_question_results.append({
            "id": item["id"],
            "expected_chunk_id": target_chunk_id,
            "expected_source_id": target_source_id,
            "language": item["language"],
            "block": item["block"],
            "chunk_hit_at": {f"k={k}": hit_at_k(chunk_ids, target_chunk_id, k) for k in K_VALUES},
            "source_hit_at": {f"k={k}": hit_at_k(source_ids, target_source_id, k) for k in K_VALUES},
            "chunk_rr": reciprocal_rank(chunk_ids, target_chunk_id),
            "source_rr": reciprocal_rank(source_ids, target_source_id),
            "top_chunk_ids": chunk_ids[:5],
        })

        if i % 10 == 0 or i == len(dataset):
            log.info("processed %d/%d", i, len(dataset))

    def avg(vals: list[float]) -> float:
        return statistics.mean(vals) if vals else 0.0

    def aggregate(items: list[dict]) -> dict:
        if not items:
            return {}
        return {
            **{f"chunk_hit@{k}": avg([r["chunk_hit_at"][f"k={k}"] for r in items]) for k in K_VALUES},
            **{f"source_hit@{k}": avg([r["source_hit_at"][f"k={k}"] for r in items]) for k in K_VALUES},
            "chunk_MRR": avg([r["chunk_rr"] for r in items]),
            "source_MRR": avg([r["source_rr"] for r in items]),
            "n": len(items),
        }

    overall = aggregate(per_question_results)

    by_lang: dict[str, list[dict]] = defaultdict(list)
    by_block: dict[str, list[dict]] = defaultdict(list)
    for r in per_question_results:
        by_lang[r["language"]].append(r)
        by_block[r["block"]].append(r)

    slices = {
        "by_language": {k: aggregate(v) for k, v in sorted(by_lang.items())},
        "by_block": {k: aggregate(v) for k, v in sorted(by_block.items())},
    }

    metrics = {
        "version": args.version,
        "config": args.config,
        "k_dense": args.k_dense,
        "k_final": args.k_final,
        "n_questions": len(dataset),
        "git_commit": get_git_commit(),
        "date": date.today().isoformat(),
        "overall": overall,
        "slices": slices,
    }

    out_path = Path(args.out) if args.out else (
        RUNS_DIR / f"{date.today().isoformat()}_rag_baseline_{args.config.replace('+','_')}_{get_git_commit()}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Метрики сохранены: %s", out_path)

    print("\n" + "=" * 80)
    print(f"RAG eval — {args.version} | config={args.config} | n={len(dataset)} | commit={get_git_commit()}")
    print("=" * 80)
    print("\n## Overall")
    print(f"  chunk_MRR   = {overall['chunk_MRR']:.3f}     source_MRR   = {overall['source_MRR']:.3f}")
    for k in K_VALUES:
        print(f"  chunk_hit@{k:<2} = {overall[f'chunk_hit@{k}']:.3f}    source_hit@{k:<2} = {overall[f'source_hit@{k}']:.3f}")

    for slice_name, slice_data in slices.items():
        print(f"\n## {slice_name}")
        for label, m in slice_data.items():
            print(f"  {label:20s} (n={m['n']:3d}): chunk_MRR={m['chunk_MRR']:.3f} | "
                  f"chunk_hit@5={m['chunk_hit@5']:.3f} | source_hit@5={m['source_hit@5']:.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
