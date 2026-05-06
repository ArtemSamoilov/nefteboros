#!/usr/bin/env python3
"""Interactive CLI для forecast-движка.

Пример:
    python scripts/forecast.py brent 3m
    python scripts/forecast.py urals 6m --method sarimax
    python scripts/forecast.py moexog 1m

См. ADR-0012.
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

# allow `python scripts/forecast.py` from repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from nefteboros.forecast.api import forecast  # noqa: E402
from nefteboros.forecast.registry import ASSET_REGISTRY  # noqa: E402
from nefteboros.forecast.schema import ForecastRefusal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forecast tool — прогноз цен нефти и газа.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Доступные активы:\n  "
            + ", ".join(sorted(ASSET_REGISTRY.keys()))
            + "\n\nГоризонты: 1m / 3m / 6m / 12m  (>= 18m → отказ + сценарии)"
        ),
    )
    parser.add_argument("asset", help="Asset ID (см. epilog)")
    parser.add_argument("horizon", help="Горизонт прогноза: 1m / 3m / 6m / 12m")
    parser.add_argument(
        "--method",
        choices=["random_walk", "sarimax", "xgboost", "ensemble"],
        help="Метод (по умолчанию — auto per horizon)",
    )
    parser.add_argument("--verbose", action="store_true", help="Включить debug-логирование")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    try:
        result = forecast(
            asset=args.asset,
            horizon=args.horizon,
            method=args.method,
        )
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if isinstance(result, ForecastRefusal):
        print(f"REFUSAL для {result.asset} @ {result.requested_horizon_months}m")
        print(f"\n{result.reason}\n")
        print("Рекомендуемые сценарные источники в RAG:")
        for s in result.redirect_to:
            print(f"  - {s}")
        return 0

    print(result.interpretation)
    print()
    print("METADATA:")
    for k, v in result.metadata.items():
        if k == "spread_per_point":
            for sp in v[:1]:
                print(f"  spread_per_point[0]: {sp}")
            if len(v) > 1:
                print(f"  ... ({len(v)} points total)")
        else:
            s = str(v)
            print(f"  {k}: {s[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
