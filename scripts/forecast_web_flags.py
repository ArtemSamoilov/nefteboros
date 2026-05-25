#!/usr/bin/env python3
"""CLI слоя «новости → флаги → μ» (ADR-0028, этап 2).

Полу-авто с approve-gate: детекция показывает предложение (diff μ + источники +
guardrails), применение — ТОЛЬКО по явному подтверждению.

Примеры:
    python scripts/forecast_web_flags.py status            # активный snapshot + μ
    python scripts/forecast_web_flags.py detect            # предложение (НЕ применяет)
    python scripts/forecast_web_flags.py detect --apply    # интерактивный approve [y/N]
    python scripts/forecast_web_flags.py detect --apply --yes        # неинтерактивный approve
    python scripts/forecast_web_flags.py detect --apply --yes --force # override guardrails
    python scripts/forecast_web_flags.py forecast brent 12m           # прогноз по активному snapshot
    python scripts/forecast_web_flags.py log               # diff-лог обновлений

См. ADR-0028, ADR-0025 (цепочка флаги→μ).
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from nefteboros.forecast.scenarios import OIL_ASSETS  # noqa: E402
from nefteboros.forecast.web_flags import (  # noqa: E402
    ApprovalRequired,
    FlagDetector,
    GuardrailBlocked,
    SnapshotStore,
    active_forecast,
    apply_proposal,
    propose_from_web,
)
from nefteboros.forecast.web_flags import guardrails as gr  # noqa: E402
from nefteboros.forecast.schema import ForecastRefusal  # noqa: E402

_YES = {"y", "yes", "да", "д"}


def _interactive_confirm() -> bool:
    try:
        ans = input("Применить это обновление калибровки? [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in _YES


def cmd_status(args) -> int:
    store = SnapshotStore(args.dir)
    a = store.load_active()
    print(f"Активный snapshot: v{a.version} (as_of {a.as_of})")
    print(f"  parent: {a.parent_version}   note: {a.note}")
    print(f"  устарел (TTL {args.ttl}ч): {store.should_refresh(args.ttl)}")
    print("  flag_states:")
    for drv, st in a.flag_states.items():
        print(f"    {drv:14s} = {st}")
    print("  μ (выводится из flag_states, не хранится):")
    for asset, mu in a.mu_all().items():
        print(f"    {asset:20s} ${mu:.2f}")
    return 0


def cmd_detect(args) -> int:
    store = SnapshotStore(args.dir)
    detector = FlagDetector()
    if not detector.searcher.has_key:
        print("BRAVE_API_KEY не задан — живая детекция недоступна. "
              "Задайте ключ в .env (см. nefteboros/search/brave.py).", file=sys.stderr)
        return 2

    proposal = propose_from_web(store, detector, cap_pct=args.cap)
    print(proposal.human_summary())

    if not proposal.has_changes:
        return 0
    if not args.apply:
        print("\n(показано предложение; применить: --apply [--yes] [--force])")
        return 0

    confirm = args.yes or _interactive_confirm()
    try:
        committed = apply_proposal(store, proposal, confirm=confirm, force=args.force)
        print(f"\n✓ Применено → активный snapshot v{committed.version}.")
        return 0
    except ApprovalRequired:
        print("\nОтменено: подтверждение не получено (approve-gate).")
        return 1
    except GuardrailBlocked as e:
        print(f"\n⚠ Заблокировано guardrails: {e}")
        return 1


def cmd_forecast(args) -> int:
    store = SnapshotStore(args.dir)
    active = store.load_active()
    res = active_forecast(args.asset, args.horizon, store=store, scenario=args.scenario)
    if isinstance(res, ForecastRefusal):
        print(f"Отказ: {res.reason}")
        return 1
    p = res.end_point
    tag = f"snapshot v{active.version}" if args.asset in OIL_ASSETS else "scenario (не нефть)"
    print(f"{args.asset} {args.horizon} [{tag}]: ${p.value:.2f} "
          f"(CI80 ${p.ci_80.low:.2f}–${p.ci_80.high:.2f})")
    print(f"  μ_0={res.metadata.get('ou_mu_0')}, flags={res.metadata.get('flag_states')}")
    return 0


def cmd_log(args) -> int:
    store = SnapshotStore(args.dir)
    entries = store.read_log()[-args.n:]
    for e in entries:
        print(f"{e.get('ts')}  {e.get('action'):9s}  v{e.get('version', '?')}  {e.get('note', '')}")
    if not entries:
        print("(diff-лог пуст)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Web-flags → μ калибровка (полу-авто с approve, ADR-0028).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dir", default=None, help="папка snapshot-хранилища (default data/state/web_flags)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="активный snapshot + μ")
    p_status.add_argument("--ttl", type=int, default=24, help="TTL свежести, часов")
    p_status.set_defaults(func=cmd_status)

    p_detect = sub.add_parser("detect", help="детекция новостей → предложение μ")
    p_detect.add_argument("--apply", action="store_true", help="предложить применить (approve-gate)")
    p_detect.add_argument("--yes", action="store_true", help="неинтерактивное подтверждение")
    p_detect.add_argument("--force", action="store_true", help="override guardrails")
    p_detect.add_argument("--cap", type=float, default=gr.DEFAULT_CAP_PCT, help="Δμ-cap (доля)")
    p_detect.set_defaults(func=cmd_detect)

    p_fc = sub.add_parser("forecast", help="прогноз по активному snapshot")
    p_fc.add_argument("asset")
    p_fc.add_argument("horizon")
    p_fc.add_argument("--scenario", default=None, choices=["base", "bear", "bull"])
    p_fc.set_defaults(func=cmd_forecast)

    p_log = sub.add_parser("log", help="diff-лог обновлений")
    p_log.add_argument("-n", type=int, default=20)
    p_log.set_defaults(func=cmd_log)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
