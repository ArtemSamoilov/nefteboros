#!/usr/bin/env python3
"""CLI саморазвития (advisory self-reflection).

Агент анализирует свои недавние трейсы и ПРЕДЛАГАЕТ улучшения в backlog. Он НЕ
применяет их и НЕ переписывает себя — человек в петле (см. ADR-0029).

Примеры:
    OUROBOROS_SELF_REFLECTION=1 python scripts/self_reflect.py run
    python scripts/self_reflect.py run --force --no-llm           # только детерминированные сигналы
    python scripts/self_reflect.py run --traces metrics/runs/<ts>/trace.jsonl
    python scripts/self_reflect.py status
    python scripts/self_reflect.py show-backlog --status open

Триггер — ТОЛЬКО эта команда (опц. cron/раз в N сессий — см. ADR-0029), не каждый
запрос. Флаг OUROBOROS_SELF_REFLECTION (default OFF) гейтит `run`; прод не зависит.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# allow `python scripts/self_reflect.py` from repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv  # noqa: E402

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from nefteboros.self_reflection import (  # noqa: E402
    ENV_FLAG,
    backlog_stats,
    default_backlog_path,
    is_enabled,
    load_entries,
    load_recent_traces,
    resolve_reflection_model,
    run_reflection,
)

_SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _explicit_paths(args: argparse.Namespace) -> list[Path] | None:
    if not getattr(args, "traces", None):
        return None
    return [Path(p) for p in args.traces]


def cmd_run(args: argparse.Namespace) -> int:
    if not is_enabled() and not args.force:
        print(
            f"{ENV_FLAG} выключен (default OFF). Включите `{ENV_FLAG}=1` "
            f"или запустите с --force для разового прогона.\n"
            "Это осознанная safety-граница: прод от рефлексии не зависит."
        )
        return 0

    result = run_reflection(
        limit=args.limit,
        explicit_paths=_explicit_paths(args),
        prefer_langfuse=not args.no_langfuse,
        use_llm=not args.no_llm,
        model=args.model,
        backlog_path=Path(args.backlog) if args.backlog else None,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "source": result.source,
                    "n_traces": result.n_traces,
                    "llm_used": result.llm_used,
                    "signals": result.signals.as_prompt_dict(),
                    "items": [
                        {
                            "observation": it.observation,
                            "suggestion": it.suggestion,
                            "severity": it.severity,
                            "category": it.category,
                            "evidence_trace_id": it.evidence_trace_id,
                            "source": it.source,
                        }
                        for it in result.items
                    ],
                    "added": result.added,
                    "backlog_path": result.backlog_path,
                    "note": result.note,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"Источник трейсов: {result.source}  |  трейсов разобрано: {result.n_traces}")
    print(f"LLM-синтез: {'да' if result.llm_used else 'нет (heuristic floor)'}")
    if result.note:
        print(f"  ! {result.note}")
    s = result.signals.as_prompt_dict()
    print(
        "Сигналы: "
        f"error_rate={s['error_rate']}  p95={s['latency_p95_ms']}мс  "
        f"refusal_rate={s['refusal_rate']}  citation_rate={s['citation_rate']}  "
        f"tool_skip={s['tool_skip_count']}  cite_gap={s['citation_node_gap_count']}"
    )
    if s["error_node_counts"]:
        print(f"  узлы-ошибки: {s['error_node_counts']}")
    print(f"\nadvisory-предложений: {len(result.items)} (новых в backlog: {result.added})")
    for it in sorted(result.items, key=lambda x: _SEV_ORDER.get(x.severity, 9)):
        ev = f"  [{it.evidence_trace_id}]" if it.evidence_trace_id else ""
        print(f"  • [{it.severity}/{it.category}] ({it.source}) {it.observation}{ev}")
        print(f"      → {it.suggestion}")
    print(f"\nbacklog: {result.backlog_path}  (advisory; ничего не применяется автоматически)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    stats = backlog_stats(Path(args.backlog) if args.backlog else None)
    model = resolve_reflection_model() or "(не задана)"
    traces, source = load_recent_traces(
        args.limit, prefer_langfuse=not args.no_langfuse
    )
    if args.json:
        print(json.dumps({**stats, "enabled": is_enabled(), "model": model,
                          "recent_traces": len(traces), "trace_source": source},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"{ENV_FLAG}: {'ON' if is_enabled() else 'OFF (default)'}")
    print(f"модель рефлексии: {model}")
    print(f"источник трейсов: {source}  |  доступно недавних: {len(traces)}")
    print(f"backlog: {stats['path']}")
    print(f"  всего: {stats['total']}  open: {stats['open']}  applied: {stats['applied']}")
    if stats["by_severity"]:
        print(f"  по severity: {stats['by_severity']}")
    if stats["by_category"]:
        print(f"  по category: {stats['by_category']}")
    if stats["last_date"]:
        print(f"  последняя запись: {stats['last_date']}")
    return 0


def cmd_show_backlog(args: argparse.Namespace) -> int:
    entries = load_entries(Path(args.backlog) if args.backlog else None)
    if args.status:
        entries = [e for e in entries if e.get("status") == args.status]
    if args.severity:
        entries = [e for e in entries if e.get("severity") == args.severity]
    entries.sort(key=lambda e: _SEV_ORDER.get(str(e.get("severity")), 9))
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    if not entries:
        print("backlog пуст (по заданному фильтру).")
        return 0
    print(f"advisory backlog — {len(entries)} запис(ь/и) (ничего не применяется автоматически):\n")
    for e in entries:
        ev = f"  evidence={e.get('evidence_trace_id')}" if e.get("evidence_trace_id") else ""
        print(
            f"• {e.get('id')}  [{e.get('severity')}/{e.get('category')}]  "
            f"{e.get('status')}  applied={e.get('applied')}  ({e.get('source')})"
        )
        print(f"    наблюдение: {e.get('observation')}")
        print(f"    предложение: {e.get('suggestion')}{ev}")
        print(f"    дата: {e.get('date')}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Advisory self-reflection — агент предлагает себе улучшения (не применяет).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="прогнать рефлексию по недавним трейсам")
    p_run.add_argument("--limit", type=int, default=50, help="сколько последних трейсов брать")
    p_run.add_argument("--force", action="store_true", help=f"игнорировать выключенный {ENV_FLAG}")
    p_run.add_argument("--no-llm", action="store_true", help="только детерминированные сигналы")
    p_run.add_argument("--no-langfuse", action="store_true", help="форсить JSONL-источник")
    p_run.add_argument("--model", help="override модели рефлексии")
    p_run.add_argument("--traces", nargs="+", help="явные пути к trace.jsonl (демо/тест)")
    p_run.add_argument("--backlog", help="override пути backlog")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_st = sub.add_parser("status", help="состояние backlog и конфигурации")
    p_st.add_argument("--limit", type=int, default=50)
    p_st.add_argument("--no-langfuse", action="store_true")
    p_st.add_argument("--backlog")
    p_st.add_argument("--json", action="store_true")
    p_st.set_defaults(func=cmd_status)

    p_sb = sub.add_parser("show-backlog", help="показать advisory backlog")
    p_sb.add_argument("--status", help="фильтр по статусу (open/...)")
    p_sb.add_argument("--severity", help="фильтр по severity")
    p_sb.add_argument("--backlog")
    p_sb.add_argument("--json", action="store_true")
    p_sb.set_defaults(func=cmd_show_backlog)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
