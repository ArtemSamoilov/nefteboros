#!/usr/bin/env python3
"""Дамп forecast() по всем активам × сценариям × горизонтам → таблица-артефакт.

Зовёт **публичный** forecast() (nefteboros.forecast.api) по комбинациям
ASSET_PARAMS (10 OU-калиброванных активов) × {base, bear, bull} ×
{1m, 3m, 6m, 12m}, плюс одну строку opec_basket для демонстрации refusal
(fetcher не реализован — P1 backlog). Собирает таблицу
(spot, μ(t)-target, mid, CI80 low/high, CI95 low/high) и сохраняет csv + json.

Точка входа намеренно — публичный forecast(), а не compute_ou_forecast: это
прогоняет реальный production-путь (live spot + derived-layer + refusal-логика
+ metadata), тот же, что использует агент-tool.

ВАЖНО — артефакт point-in-time:
  - OU-параметры заморожены (scenarios.AS_OF_DATE = 2026-05-08).
  - spot фетчится live (yfinance / MOEX ISS) на момент прогона — будет дрейфовать.
  При расхождении даты прогона с AS_OF_DATE > REVIEW_AFTER_DAYS (14 дней) артефакт
  помечается snapshot_stale=true. Таблицу можно регенерить, когда этап 2 (web→flags)
  даст актуальные флаги под текущий рынок.

Использование:
    python scripts/forecast_table.py
    python scripts/forecast_table.py --horizons 12m
    python scripts/forecast_table.py --assets brent,wti --scenarios base,bull
    python scripts/forecast_table.py --out-dir docs/report

См. ADR-0024 (модель OU), docs/report/forecast-section.md (отчёт).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import subprocess
import sys
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

# allow `python scripts/forecast_table.py` from repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from nefteboros.forecast.api import forecast  # noqa: E402
from nefteboros.forecast.registry import get_asset  # noqa: E402
from nefteboros.forecast.schema import ForecastRefusal  # noqa: E402
from nefteboros.forecast.scenarios import (  # noqa: E402
    AS_OF_DATE,
    ASSET_PARAMS,
    REVIEW_AFTER_DAYS,
    SCENARIO_NAMES,
)

logger = logging.getLogger("forecast_table")

DEFAULT_ASSETS: list[str] = list(ASSET_PARAMS.keys())  # 10 OU-калиброванных
DEFAULT_HORIZONS: list[str] = ["1m", "3m", "6m", "12m"]
DEFAULT_SCENARIOS: list[str] = list(SCENARIO_NAMES)  # base, bear, bull

# opec_basket: одна строка для документирования refusal (не в ASSET_PARAMS).
OPEC_DEMO_ASSET = "opec_basket"

CSV_FIELDS: list[str] = [
    "asset",
    "unit",
    "scenario",
    "horizon",
    "status",  # ok | refusal | error
    "spot",
    "spot_obs_date",
    "mu_t_target",  # μ(t) = μ_0 × (1 + inflation·t) — long-run target на горизонте t
    "mid",
    "ci80_low",
    "ci80_high",
    "ci95_low",
    "ci95_high",
    "target_date",
    "method",
    "note",  # reason для refusal / сообщение для error
]


def _round(x: float | None, n: int = 2) -> float | None:
    return round(float(x), n) if x is not None else None


def _empty_row(asset: str, unit: str | None, scenario: str, horizon: str) -> dict:
    row = {field: None for field in CSV_FIELDS}
    row.update(asset=asset, unit=unit, scenario=scenario, horizon=horizon)
    return row


def run_one(asset: str, scenario: str, horizon: str) -> dict:
    """Один вызов forecast(); возвращает плоскую строку под CSV_FIELDS."""
    try:
        unit = get_asset(asset).unit
    except KeyError:
        unit = None
    row = _empty_row(asset, unit, scenario, horizon)

    try:
        res = forecast(asset, horizon, scenario=scenario)
    except Exception as exc:  # live API / fetch failure — фиксируем, не валим прогон
        logger.warning("forecast(%s, %s, %s) упал: %s", asset, horizon, scenario, exc)
        row.update(status="error", note=f"{type(exc).__name__}: {exc}")
        return row

    if isinstance(res, ForecastRefusal):
        row.update(status="refusal", note=res.reason)
        return row

    point = res.points[0]
    meta = res.metadata
    target = point.date
    target_str = target.date().isoformat() if hasattr(target, "date") else str(target)
    row.update(
        status="ok",
        spot=_round(meta.get("spot")),
        spot_obs_date=meta.get("spot_observation_date"),
        mu_t_target=_round(meta.get("ou_mu_t")),
        mid=_round(point.value),
        ci80_low=_round(point.ci_80.low),
        ci80_high=_round(point.ci_80.high),
        ci95_low=_round(point.ci_95.low),
        ci95_high=_round(point.ci_95.high),
        target_date=target_str,
        method=res.method.value,
        note="",
    )
    return row


def build_rows(
    assets: list[str],
    scenarios: list[str],
    horizons: list[str],
    *,
    include_opec_demo: bool,
) -> list[dict]:
    rows: list[dict] = []
    for asset in assets:
        for scenario in scenarios:
            for horizon in horizons:
                rows.append(run_one(asset, scenario, horizon))
    if include_opec_demo and OPEC_DEMO_ASSET not in assets:
        # Одна репрезентативная строка: refusal не зависит от scenario/horizon.
        rows.append(run_one(OPEC_DEMO_ASSET, "base", "12m"))
    return rows


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _print_summary(meta: dict, rows: list[dict]) -> None:
    stale_tag = " ⚠ STALE" if meta["snapshot_stale"] else ""
    print(
        f"\nsnapshot as_of={meta['as_of_date']} | generated={meta['generated_at'][:10]} "
        f"| days_since={meta['days_since_as_of']} (review_after={meta['review_after_days']}d){stale_tag}"
    )
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"rows={len(rows)} " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    print("\n=== 12m (год вперёд) ===")
    hdr = f"{'asset':<20}{'scen':<6}{'spot':>10}{'mu_t':>10}{'mid':>10}{'CI80':>22}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if r["horizon"] != "12m":
            continue
        if r["status"] != "ok":
            print(f"{r['asset']:<20}{r['scenario']:<6}{'—':>10}{'—':>10}{'—':>10}{r['status']:>22}")
            continue
        ci = f"[{r['ci80_low']:.1f}, {r['ci80_high']:.1f}]"
        print(
            f"{r['asset']:<20}{r['scenario']:<6}{r['spot']:>10.2f}"
            f"{r['mu_t_target']:>10.2f}{r['mid']:>10.2f}{ci:>22}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Дамп forecast() по всем активам/сценариям/горизонтам в csv+json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Активы по умолчанию (ASSET_PARAMS):\n  "
            + ", ".join(DEFAULT_ASSETS)
            + f"\nСценарии: {', '.join(DEFAULT_SCENARIOS)}"
            + f"\nГоризонты: {', '.join(DEFAULT_HORIZONS)}"
        ),
    )
    parser.add_argument("--assets", help="csv-список активов (default — все ASSET_PARAMS)")
    parser.add_argument("--scenarios", help="csv-список сценариев (default base,bear,bull)")
    parser.add_argument("--horizons", help="csv-список горизонтов (default 1m,3m,6m,12m)")
    parser.add_argument(
        "--out-dir",
        default="docs/report",
        help="каталог для артефактов (default docs/report)",
    )
    parser.add_argument(
        "--basename",
        default="forecast-table",
        help="базовое имя файлов артефакта (default forecast-table)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    def _split(val: str | None, default: list[str]) -> list[str]:
        if not val:
            return default
        return [s.strip() for s in val.split(",") if s.strip()]

    assets = _split(args.assets, DEFAULT_ASSETS)
    scenarios = _split(args.scenarios, DEFAULT_SCENARIOS)
    horizons = _split(args.horizons, DEFAULT_HORIZONS)
    include_opec_demo = args.assets is None  # только в полном дефолтном прогоне

    rows = build_rows(assets, scenarios, horizons, include_opec_demo=include_opec_demo)

    today = date.today()
    days_since = (today - AS_OF_DATE).days
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "python_version": sys.version.split()[0],
        "as_of_date": AS_OF_DATE.isoformat(),
        "review_after_days": REVIEW_AFTER_DAYS,
        "days_since_as_of": days_since,
        "snapshot_stale": days_since > REVIEW_AFTER_DAYS,
        "assets": assets,
        "scenarios": scenarios,
        "horizons": horizons,
        "n_rows": len(rows),
        "columns": CSV_FIELDS,
        "note": (
            "spot фетчится live на момент generated_at; OU-параметры заморожены на "
            "as_of_date. Артефакт — point-in-time. См. docs/report/forecast-section.md."
        ),
    }

    out_dir = (ROOT / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.basename}.json"
    csv_path = out_dir / f"{args.basename}.csv"

    with json_path.open("w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "rows": rows}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    _print_summary(meta, rows)
    print(f"\nartifact json: {json_path.relative_to(ROOT)}")
    print(f"artifact csv:  {csv_path.relative_to(ROOT)}")

    n_ok = sum(1 for r in rows if r["status"] == "ok")
    if n_ok == 0:
        logger.error("ни одной успешной строки — проверь сеть/источники")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
