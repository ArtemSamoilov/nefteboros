#!/usr/bin/env python3
"""Walk-forward backtest для OU production path (ADR-0024, Track A5).

Тестирует regime-conditioned mean-reverting OU forecast на исторических
snapshots. Это **тест анахронистической устойчивости**: параметры
ASSET_PARAMS статически calibrated под 2026-05 shock; backtest показывает,
насколько ошибочны были бы наши 2026-05 параметры на 2024/2025 рынке.

Метрики per (asset, scenario, horizon):
  - MAPE на mid (mean absolute % error)
  - Bias (mean signed % error — положительный = под-прогноз, отрицательный = пере-)
  - Coverage 80% / 95% — % попаданий realized в CI

Также — segmentation per BacktestRegime (PRE_2022, RUSSIA_WAR_SHOCK,
CAP_NORMALIZATION, IRAN_2026) для визуализации, в каком режиме calibration
applicable best.

Использование:
    python -m scripts.eval.eval_ou
    python -m scripts.eval.eval_ou --assets brent,wti --horizons 3m,6m
    python -m scripts.eval.eval_ou --quick

Output: metrics/runs/<timestamp>_ou_walkforward.json

См. ADR-0024 §A5.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import subprocess
import sys
import warnings
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

# Allow `python scripts/eval/eval_ou.py` from repo root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from nefteboros.forecast.data.eia import fetch_eia_for_asset  # noqa: E402
from nefteboros.forecast.data.moex import fetch_moex  # noqa: E402
from nefteboros.forecast.data.yf import fetch_yfinance  # noqa: E402
from nefteboros.forecast.registry import get_asset  # noqa: E402
from nefteboros.forecast.scenarios import (  # noqa: E402
    ASSET_PARAMS,
    SCENARIO_NAMES,
    compute_ou_forecast,
)
from nefteboros.forecast.schema import BacktestRegime, DataSource  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Default settings — overridable via CLI
DEFAULT_ASSETS = [
    "brent", "wti",
    "henry_hub", "ttf",
    "moexog", "gazp", "nvtk",
]
DEFAULT_HORIZONS = [1, 3, 6, 12]
DEFAULT_HISTORY_YEARS = 5.0
DEFAULT_ORIGIN_STEP_DAYS = 30  # monthly rolling origin


# =============================================================================
# Regime detection (по дате)
# =============================================================================


def detect_regime(d: pd.Timestamp) -> str:
    """Регим per BacktestRegime convention. Календарные сегменты."""
    dd = d.date() if hasattr(d, "date") else d
    if dd < date(2022, 2, 24):
        return BacktestRegime.PRE_2022.value
    if dd < date(2022, 12, 31):
        return BacktestRegime.RUSSIA_WAR_SHOCK.value
    if dd < date(2026, 1, 1):
        return BacktestRegime.CAP_NORMALIZATION.value
    return BacktestRegime.IRAN_2026.value


# =============================================================================
# History loading
# =============================================================================


def fetch_history(asset: str, years: float, use_cache: bool = True) -> pd.Series:
    """Fetch full historical price series."""
    since = pd.Timestamp.now(tz="UTC").normalize() - pd.DateOffset(
        years=int(math.ceil(years))
    )
    meta = get_asset(asset)
    src = meta.primary_source

    if src == DataSource.YFINANCE:
        return fetch_yfinance(asset, since=since, use_cache=use_cache)
    if src == DataSource.EIA:
        return fetch_eia_for_asset(asset, since=since, use_cache=use_cache)
    if src == DataSource.MOEX_ISS:
        return fetch_moex(asset, since=since, use_cache=use_cache)
    raise ValueError(f"asset {asset!r} primary_source={src.value!r} unsupported")


# =============================================================================
# Walk-forward backtest
# =============================================================================


def walk_forward_for_asset(
    asset: str,
    horizons_months: list[int],
    origin_step_days: int,
    history_years: float,
) -> list[dict]:
    """Прогнать walk-forward для одного актива.

    Returns: list of records (one per (date, scenario, horizon) tuple).
    """
    logger.info("walk_forward asset=%s ...", asset)
    try:
        history = fetch_history(asset, history_years)
    except Exception as e:
        logger.warning("fetch failed for %s: %s", asset, e)
        return [{"asset": asset, "error": f"fetch failed: {type(e).__name__}: {e}"}]

    if history.empty or len(history) < 60:
        return [{"asset": asset, "error": f"insufficient history n={len(history)}"}]

    # Generate rolling origin dates
    start = history.index.min() + pd.Timedelta(days=30)
    end = history.index.max() - pd.Timedelta(days=max(horizons_months) * 31 + 5)
    if end <= start:
        return [{"asset": asset, "error": "history too short for max horizon"}]
    origins = pd.date_range(
        start=start, end=end, freq=f"{origin_step_days}D"
    ).intersection(history.index)

    records: list[dict] = []
    for T in origins:
        spot = float(history.loc[T])
        regime = detect_regime(T)

        for scenario in SCENARIO_NAMES:
            params = ASSET_PARAMS[asset][scenario]
            for h in horizons_months:
                target_date = T + pd.DateOffset(months=h)
                # Find closest realized date (within 7 days)
                future_window = history[history.index >= target_date]
                if future_window.empty:
                    continue
                realized_date = future_window.index[0]
                if (realized_date - target_date).days > 7:
                    continue
                realized = float(future_window.iloc[0])

                # OU forecast
                forecast = compute_ou_forecast(
                    spot=spot, params=params, horizon_months=h,
                )
                err = realized - forecast.mid
                pct_err = err / realized * 100 if realized != 0 else 0.0
                in_ci_80 = forecast.ci_80_low <= realized <= forecast.ci_80_high
                in_ci_95 = forecast.ci_95_low <= realized <= forecast.ci_95_high

                records.append({
                    "asset": asset,
                    "origin_date": T.strftime("%Y-%m-%d"),
                    "target_date": realized_date.strftime("%Y-%m-%d"),
                    "regime": regime,
                    "scenario": scenario,
                    "horizon_months": h,
                    "spot": round(spot, 4),
                    "realized": round(realized, 4),
                    "forecast_mid": round(forecast.mid, 4),
                    "ci_80_low": round(forecast.ci_80_low, 4),
                    "ci_80_high": round(forecast.ci_80_high, 4),
                    "ci_95_low": round(forecast.ci_95_low, 4),
                    "ci_95_high": round(forecast.ci_95_high, 4),
                    "abs_pct_error": round(abs(pct_err), 4),
                    "signed_pct_error": round(pct_err, 4),
                    "in_ci_80": in_ci_80,
                    "in_ci_95": in_ci_95,
                })

    logger.info("  %s: %d records", asset, len(records))
    return records


# =============================================================================
# Aggregation
# =============================================================================


def aggregate(records: list[dict]) -> dict:
    """Aggregate metrics per (asset, scenario, horizon) и per regime."""
    df = pd.DataFrame([r for r in records if "error" not in r])
    if df.empty:
        return {"summary": {}, "regimes": {}, "errors": records}

    # Per (asset, scenario, horizon)
    grouped = df.groupby(["asset", "scenario", "horizon_months"]).agg(
        n=("abs_pct_error", "count"),
        mape=("abs_pct_error", "mean"),
        bias=("signed_pct_error", "mean"),
        median_abs_err=("abs_pct_error", "median"),
        coverage_80=("in_ci_80", "mean"),
        coverage_95=("in_ci_95", "mean"),
    ).round(3).reset_index()

    summary = {}
    for _, row in grouped.iterrows():
        key = f"{row['asset']}__{row['scenario']}__h{row['horizon_months']}m"
        summary[key] = {
            "n": int(row["n"]),
            "mape_pct": float(row["mape"]),
            "bias_pct": float(row["bias"]),
            "median_abs_err_pct": float(row["median_abs_err"]),
            "coverage_80": float(row["coverage_80"]),
            "coverage_95": float(row["coverage_95"]),
        }

    # Per regime per (asset, scenario, horizon)
    regimes = {}
    by_regime = df.groupby(
        ["regime", "asset", "scenario", "horizon_months"]
    ).agg(
        n=("abs_pct_error", "count"),
        mape=("abs_pct_error", "mean"),
        coverage_80=("in_ci_80", "mean"),
    ).round(3).reset_index()
    for _, row in by_regime.iterrows():
        regime = row["regime"]
        if regime not in regimes:
            regimes[regime] = {}
        key = f"{row['asset']}__{row['scenario']}__h{row['horizon_months']}m"
        regimes[regime][key] = {
            "n": int(row["n"]),
            "mape_pct": float(row["mape"]),
            "coverage_80": float(row["coverage_80"]),
        }

    errors = [r for r in records if "error" in r]
    return {"summary": summary, "regimes": regimes, "errors": errors}


# =============================================================================
# CLI
# =============================================================================


def get_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
        ).strip()
    except Exception:
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description="OU walk-forward backtest (ADR-0024 A5)")
    parser.add_argument(
        "--assets", default=",".join(DEFAULT_ASSETS),
        help=f"Comma-sep. Default: {','.join(DEFAULT_ASSETS)}",
    )
    parser.add_argument(
        "--horizons", default="1m,3m,6m,12m",
        help="Comma-sep horizons. Default: 1m,3m,6m,12m",
    )
    parser.add_argument(
        "--history-years", type=float, default=DEFAULT_HISTORY_YEARS,
        help=f"Default: {DEFAULT_HISTORY_YEARS}",
    )
    parser.add_argument(
        "--origin-step-days", type=int, default=DEFAULT_ORIGIN_STEP_DAYS,
        help=f"Rolling origin step. Default: {DEFAULT_ORIGIN_STEP_DAYS}",
    )
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: brent only, 3m only.")
    parser.add_argument("--output-dir", default=str(ROOT / "metrics" / "runs"))
    args = parser.parse_args()

    if args.quick:
        assets = ["brent"]
        horizons_months = [3]
    else:
        assets = [a.strip() for a in args.assets.split(",") if a.strip()]
        horizons_str = [h.strip() for h in args.horizons.split(",") if h.strip()]
        horizons_months = [
            int(h.rstrip("m")) for h in horizons_str
        ]

    logger.warning(
        "Walk-forward: assets=%s horizons=%s history=%.1fy step=%dd",
        assets, horizons_months, args.history_years, args.origin_step_days,
    )

    all_records: list[dict] = []
    for asset in assets:
        if asset not in ASSET_PARAMS:
            logger.warning("skip %s — no OU calibration", asset)
            continue
        records = walk_forward_for_asset(
            asset, horizons_months, args.origin_step_days, args.history_years,
        )
        all_records.extend(records)

    aggr = aggregate(all_records)

    # Write JSON
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sha = get_git_sha()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{timestamp}_ou_walkforward_{sha}.json"

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "config": {
            "assets": assets,
            "horizons_months": horizons_months,
            "history_years": args.history_years,
            "origin_step_days": args.origin_step_days,
        },
        "summary": aggr["summary"],
        "by_regime": aggr["regimes"],
        "errors": aggr["errors"],
        "n_total_records": len([r for r in all_records if "error" not in r]),
    }

    with out_path.open("w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"Saved: {out_path}")
    print(f"Total records: {output['n_total_records']}")
    print()

    # Print short summary table
    print("=== Summary (mape_pct, coverage_80) per (asset, scenario, horizon) ===")
    print(f"{'asset':12s} {'scenario':6s} {'h':>3s} {'n':>4s} {'mape%':>8s} {'bias%':>8s} {'cov80':>6s}")
    for key in sorted(aggr["summary"].keys()):
        row = aggr["summary"][key]
        asset, scenario, hkey = key.split("__")
        print(
            f"{asset:12s} {scenario:6s} {hkey:>3s} {row['n']:>4d} "
            f"{row['mape_pct']:>8.2f} {row['bias_pct']:>+8.2f} "
            f"{row['coverage_80']:>6.2f}"
        )


if __name__ == "__main__":
    main()
