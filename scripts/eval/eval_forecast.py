#!/usr/bin/env python3
"""Запустить walk-forward бектест для сетки (asset × method × horizon).

Сохраняет результат в metrics/runs/<date>_forecast_<sha>.json для последующего
использования в docs/experiments/forecast.md.

Поддерживает incremental cache: если key уже есть в JSON — skip.

Использование:
    python scripts/eval/eval_forecast.py
    python scripts/eval/eval_forecast.py --assets brent,wti --horizons 3m,6m
    python scripts/eval/eval_forecast.py --quick

См. ADR-0012 §«Бектест».
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# allow `python scripts/eval/eval_forecast.py` from repo root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from nefteboros.forecast.backtest import run_backtest  # noqa: E402
from nefteboros.forecast.data.eia import fetch_eia_for_asset  # noqa: E402
from nefteboros.forecast.data.moex import fetch_moex  # noqa: E402
from nefteboros.forecast.data.yf import fetch_yfinance  # noqa: E402
from nefteboros.forecast.models.ensemble import EnsembleForecaster  # noqa: E402
from nefteboros.forecast.models.random_walk import RandomWalkForecaster  # noqa: E402
from nefteboros.forecast.models.sarimax import SARIMAXForecaster  # noqa: E402
from nefteboros.forecast.models.xgboost_m import XGBoostForecaster  # noqa: E402
from nefteboros.forecast.registry import get_asset  # noqa: E402
from nefteboros.forecast.schema import (  # noqa: E402
    BacktestRegime,
    DataSource,
    Horizon,
    ModelMethod,
)


logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


OBSERVABLE_ASSETS = ["brent", "wti", "henry_hub", "ttf", "moexog", "gazp", "nvtk"]

ALL_METHODS = [
    ModelMethod.RANDOM_WALK,
    ModelMethod.SARIMAX,
    ModelMethod.XGBOOST,
    ModelMethod.ENSEMBLE,
]

ALL_HORIZONS = [Horizon.M1, Horizon.M3, Horizon.M6, Horizon.M12]


def _build_model(method: ModelMethod):
    if method == ModelMethod.RANDOM_WALK:
        return lambda: RandomWalkForecaster()
    if method == ModelMethod.SARIMAX:
        return lambda: SARIMAXForecaster()
    if method == ModelMethod.XGBOOST:
        return lambda: XGBoostForecaster()
    if method == ModelMethod.ENSEMBLE:
        return lambda: EnsembleForecaster([SARIMAXForecaster(), XGBoostForecaster()])
    raise ValueError(method)


def _fetch_history(asset: str) -> pd.Series:
    meta = get_asset(asset)
    since = pd.Timestamp.now(tz="UTC").normalize() - pd.DateOffset(years=5)

    if meta.primary_source == DataSource.YFINANCE:
        return fetch_yfinance(asset, since=since, use_cache=True)
    if meta.primary_source == DataSource.EIA:
        return fetch_eia_for_asset(asset, since=since, use_cache=True)
    if meta.primary_source == DataSource.MOEX_ISS:
        return fetch_moex(asset, since=since, use_cache=True)
    raise ValueError(f"unsupported source for {asset}: {meta.primary_source}")


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def _result_to_dict(summary, asset: str, method: ModelMethod, horizon: Horizon) -> dict:
    return {
        "asset": asset,
        "method": method.value,
        "horizon": horizon.value,
        "train_window_years": summary.train_window_years,
        "history_window_years": summary.history_window_years,
        "rolling_step_months": summary.rolling_step_months,
        "per_regime": [
            {
                "regime": m.regime.value,
                "n_forecasts": m.n_forecasts,
                "mape": m.mape,
                "rmse": m.rmse,
                "coverage_80": m.coverage_80,
                "coverage_95": m.coverage_95,
                "mase_vs_rw": m.mase_vs_rw,
                "directional_accuracy": m.directional_accuracy,
            }
            for m in summary.per_regime
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward бектест forecast-моделей.")
    parser.add_argument("--assets", help="Comma-separated asset ids; default — все observable.")
    parser.add_argument("--methods", help="Comma-separated method ids.")
    parser.add_argument("--horizons", help="Comma-separated horizons (1m/3m/6m/12m).")
    parser.add_argument("--train-years", type=float, default=3.0)
    parser.add_argument("--step-months", type=int, default=1)
    parser.add_argument("--quick", action="store_true",
                        help="Сокращённый прогон для smoke (1 asset, 2 method, 1 horizon).")
    parser.add_argument("--out", default=None, help="Путь к выходному JSON.")
    args = parser.parse_args()

    if args.quick:
        assets = ["brent"]
        methods = [ModelMethod.RANDOM_WALK, ModelMethod.SARIMAX]
        horizons = [Horizon.M3]
    else:
        assets = args.assets.split(",") if args.assets else OBSERVABLE_ASSETS
        methods = (
            [ModelMethod(m) for m in args.methods.split(",")]
            if args.methods else ALL_METHODS
        )
        horizons = (
            [Horizon(h) for h in args.horizons.split(",")]
            if args.horizons else ALL_HORIZONS
        )

    sha = _git_sha()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = ROOT / "metrics" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else out_dir / f"{today}_forecast_{sha}.json"

    cache: dict[str, dict] = {}
    if out_path.exists():
        try:
            cache = json.loads(out_path.read_text())
            print(f"loaded cache: {len(cache)} existing keys from {out_path}")
        except Exception as e:
            print(f"WARN: cache file unreadable, starting fresh: {e}")
            cache = {}

    n_total = len(assets) * len(methods) * len(horizons)
    n_done = n_skipped = n_failed = 0

    for asset in assets:
        meta = get_asset(asset)
        log_t = meta.log_transform
        try:
            history = _fetch_history(asset)
            if history.empty:
                print(f"SKIP {asset}: empty history")
                continue
        except Exception as e:
            print(f"FAIL fetch {asset}: {e}")
            continue

        for method in methods:
            if method not in meta.available_methods:
                continue

            for horizon in horizons:
                key = f"{asset}__{method.value}__{horizon.value}"
                if key in cache:
                    n_skipped += 1
                    continue

                idx = n_done + n_skipped + n_failed + 1
                print(f"[{idx}/{n_total}] {key} ...", end=" ", flush=True)
                try:
                    summary = run_backtest(
                        history=history,
                        model_factory=_build_model(method),
                        method=method,
                        asset=asset,
                        horizon=horizon,
                        train_window_years=args.train_years,
                        step_months=args.step_months,
                        log_transform=log_t,
                    )
                    cache[key] = _result_to_dict(summary, asset, method, horizon)
                    agg = next(
                        (m for m in summary.per_regime if m.regime == BacktestRegime.AGGREGATE),
                        None,
                    )
                    if agg:
                        mape_s = f"{agg.mape:.2f}%" if agg.mape is not None else "N/A"
                        mase_s = f"{agg.mase_vs_rw:.2f}" if agg.mase_vs_rw is not None else "N/A"
                        print(f"OK n={agg.n_forecasts} MAPE={mape_s} MASE={mase_s}")
                    n_done += 1
                except Exception as e:
                    print(f"FAIL: {type(e).__name__}: {e}")
                    n_failed += 1
                out_path.write_text(json.dumps(cache, indent=2, default=str))

    print(
        f"\nDone. {n_done} computed, {n_skipped} cached, {n_failed} failed. "
        f"Output: {out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
