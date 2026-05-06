"""Eval intent classifier на golden dataset.

Запуск:
    python scripts/eval/eval_intent_classifier.py [--llm/--no-llm]
                                                  [--dataset PATH]
                                                  [--out-dir PATH]

Метрики:
- type_accuracy             — доля правильно определённых intent.type (4 класса)
- assets_jaccard_mean       — средний Jaccard expected vs predicted assets
                              (только для forecast-типов)
- horizon_match_rate        — exact match горизонта (когда expected или actual != null)
- per_class precision/recall/F1
- confusion matrix (expected → predicted)
- per_query trace (для разбора failures)
- per_category accuracy (rule-based категории датасета)

Run modes:
- --no-llm  — только rule-based (baseline). Default.
- --llm     — rule-based + llm_disambiguate fallback на no_keyword_match.

Без сетевых вызовов в --no-llm. С --llm требует GIGACHAT_* env (грузим
через python-dotenv из ближайшего .env).

Output:
- stdout: краткий summary.
- metrics/runs/<date>_intent_<rules|llm>_<sha>.json: полный artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Eval intent classifier (rule-based or hybrid with GigaChat LLM).",
    )
    p.add_argument(
        "--dataset",
        default=str(REPO_ROOT / "datasets" / "intent_classifier.jsonl"),
    )
    p.add_argument(
        "--llm",
        action="store_true",
        help="включить llm_disambiguate fallback на no_keyword_match",
    )
    p.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "metrics" / "runs"),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="опционально: прогнать только первые N примеров (для smoke)",
    )
    return p.parse_args()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            cwd=REPO_ROOT,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _load_dataset(path: str) -> list[dict[str, Any]]:
    examples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            examples.append(json.loads(line))
    return examples


def _classify_one(query: str, *, use_llm: bool):
    """Полный pipeline classify (rule-based + optional LLM fallback)."""
    from nefteboros.graphs.intents import classify_intent

    intent = classify_intent(query)
    if not use_llm:
        return intent
    if intent.matched_rule != "no_keyword_match":
        return intent

    from nefteboros.graphs.nodes.llm_disambiguate import llm_disambiguate
    from nefteboros.graphs.state import GraphState

    state = GraphState(query=query, intent=intent)
    try:
        result = asyncio.run(llm_disambiguate(state))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! llm_disambiguate raised {type(exc).__name__}: {exc}")
        return intent
    new_intent = result.get("intent")
    return new_intent if new_intent is not None else intent


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _evaluate(examples: list[dict[str, Any]], *, use_llm: bool) -> dict[str, Any]:
    type_correct = 0
    horizon_correct = 0
    horizon_applicable = 0
    jaccards: list[float] = []

    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_query: list[dict[str, Any]] = []
    per_category_correct: dict[str, dict[str, int]] = defaultdict(
        lambda: {"correct": 0, "total": 0}
    )

    n = len(examples)
    for idx, ex in enumerate(examples, 1):
        if idx % 10 == 0:
            print(f"  [{idx}/{n}] processed")

        query = ex["query"]
        expected_type = ex["expected_type"]
        expected_assets = ex.get("expected_assets") or []
        expected_horizon = ex.get("expected_horizon")
        category = ex.get("category", "uncategorized")

        intent = _classify_one(query, use_llm=use_llm)
        actual_type = intent.type.value
        actual_assets = list(intent.forecast_assets)
        actual_horizon = (
            intent.forecast_horizon.value if intent.forecast_horizon else None
        )

        confusion[expected_type][actual_type] += 1

        type_match = actual_type == expected_type
        if type_match:
            type_correct += 1

        per_category_correct[category]["total"] += 1
        if type_match:
            per_category_correct[category]["correct"] += 1

        if expected_type in ("forecast_simple", "forecast_with_context"):
            jaccards.append(_jaccard(expected_assets, actual_assets))
            if expected_horizon is not None or actual_horizon is not None:
                horizon_applicable += 1
                if expected_horizon == actual_horizon:
                    horizon_correct += 1

        per_query.append(
            {
                "query": query,
                "category": category,
                "expected": {
                    "type": expected_type,
                    "assets": expected_assets,
                    "horizon": expected_horizon,
                },
                "actual": {
                    "type": actual_type,
                    "assets": actual_assets,
                    "horizon": actual_horizon,
                    "matched_rule": intent.matched_rule,
                },
                "type_match": type_match,
            }
        )

    types = sorted(
        {ex["expected_type"] for ex in examples}
        | {p["actual"]["type"] for p in per_query}
    )
    per_class: dict[str, dict[str, Any]] = {}
    for t in types:
        tp = sum(
            1 for p in per_query
            if p["expected"]["type"] == t and p["actual"]["type"] == t
        )
        fp = sum(
            1 for p in per_query
            if p["expected"]["type"] != t and p["actual"]["type"] == t
        )
        fn = sum(
            1 for p in per_query
            if p["expected"]["type"] == t and p["actual"]["type"] != t
        )
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
        per_class[t] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "support": tp + fn,
        }

    per_category: dict[str, float] = {
        cat: round(v["correct"] / v["total"], 3) if v["total"] else 0.0
        for cat, v in sorted(per_category_correct.items())
    }

    return {
        "n": n,
        "use_llm": use_llm,
        "type_accuracy": round(type_correct / n, 3),
        "horizon_match_rate": (
            round(horizon_correct / horizon_applicable, 3)
            if horizon_applicable
            else None
        ),
        "horizon_applicable_n": horizon_applicable,
        "assets_jaccard_mean": (
            round(sum(jaccards) / len(jaccards), 3) if jaccards else None
        ),
        "assets_jaccard_n": len(jaccards),
        "per_class": per_class,
        "per_category_accuracy": per_category,
        "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
        "per_query": per_query,
    }


def _print_summary(metrics: dict[str, Any]) -> None:
    print()
    print("=" * 60)
    print(f"Results (use_llm={metrics['use_llm']}, n={metrics['n']}):")
    print("=" * 60)
    print(f"  type_accuracy        : {metrics['type_accuracy']}")
    print(
        f"  horizon_match_rate   : {metrics['horizon_match_rate']} "
        f"({metrics['horizon_applicable_n']} applicable)"
    )
    print(
        f"  assets_jaccard_mean  : {metrics['assets_jaccard_mean']} "
        f"({metrics['assets_jaccard_n']} applicable)"
    )
    print()
    print("  per_class precision/recall/F1:")
    for t, m in metrics["per_class"].items():
        print(
            f"    {t:24s} P={m['precision']:.3f} R={m['recall']:.3f} "
            f"F1={m['f1']:.3f}  n={m['support']}"
        )
    print()
    print("  per_category accuracy:")
    for cat, acc in metrics["per_category_accuracy"].items():
        print(f"    {cat:32s} {acc:.3f}")
    print()
    print("  confusion matrix (expected → predicted):")
    for expected, preds in metrics["confusion_matrix"].items():
        for predicted, count in preds.items():
            mark = "✓" if expected == predicted else "✗"
            print(f"    {mark} {expected:24s} → {predicted:24s} {count}")


def main() -> int:
    args = _parse_args()

    if args.llm:
        try:
            from dotenv import find_dotenv, load_dotenv
            env_path = find_dotenv(usecwd=True) or find_dotenv()
            if env_path:
                load_dotenv(env_path)
                print(f"Loaded .env from {env_path}")
            else:
                print("No .env found via find_dotenv; relying on shell env.")
        except ImportError:
            print("python-dotenv не установлен; полагаемся на shell env.")

    examples = _load_dataset(args.dataset)
    if args.limit is not None:
        examples = examples[: args.limit]
    print(f"Loaded {len(examples)} examples from {args.dataset}")

    print(f"Evaluating with use_llm={args.llm}...")
    metrics = _evaluate(examples, use_llm=args.llm)

    _print_summary(metrics)

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = _git_sha()
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "llm" if args.llm else "rules"
    out_file = out_dir / f"{date}_intent_{suffix}_{sha}.json"

    metrics_with_meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "dataset_path": args.dataset,
        **metrics,
    }
    out_file.write_text(
        json.dumps(metrics_with_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
