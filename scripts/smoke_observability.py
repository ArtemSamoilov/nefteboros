"""Smoke-тест observability — проверка что JSON-trace пишется без падений.

Запуск:
    LANGFUSE_ENABLED=false python scripts/smoke_observability.py

Ожидание:
    - 3 диалога прогоняются через analyst_graph.
    - В metrics/runs/<utcnow>/trace.jsonl есть span'ы и trace-summary.
    - Граф не падает.
    - При LANGFUSE_ENABLED=false — никаких HTTP-запросов в Langfuse.

Используется ad-hoc (out-of-PR) для быстрой проверки. Production e2e —
в Track D (`scripts/eval/eval_e2e.py`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


async def _smoke() -> int:
    # Изолируем JSON-trace в tmp, чтобы не мусорить metrics/runs/.
    tmp_run_dir = pathlib.Path(tempfile.mkdtemp(prefix="nefteboros_smoke_"))
    os.environ["OBSERVABILITY_RUN_DIR"] = str(tmp_run_dir)
    # Гарантированно отключаем Langfuse для этого smoke.
    os.environ["LANGFUSE_ENABLED"] = "false"

    print(f"[smoke] OBSERVABILITY_RUN_DIR = {tmp_run_dir}")

    from nefteboros.graphs.analyst_graph import build_analyst_graph, invoke_with_trace
    from nefteboros.graphs.state import GraphState

    graph = build_analyst_graph()

    test_queries = [
        "прогноз brent на 3 месяца",  # forecast_simple
        "что такое OPEC+",  # out_of_scope (off-topic для рамок forecast)
        "цены на российский газ",  # russian_gas_refusal
    ]

    for q in test_queries:
        print(f"\n[smoke] query: {q!r}")
        try:
            state = GraphState(query=q)
            result = await invoke_with_trace(graph, state)
            synthesis = (result.get("synthesis") if isinstance(result, dict) else None)
            preview = (synthesis or "<empty>")[:120]
            print(f"[smoke]   synthesis preview: {preview!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[smoke]   FAILED: {type(exc).__name__}: {exc}")
            return 1

    # Проверим JSON-trace.
    trace_file = tmp_run_dir / "trace.jsonl"
    if not trace_file.exists():
        print(f"[smoke] FAIL: {trace_file} не создан")
        return 1

    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
    print(f"\n[smoke] trace.jsonl: {len(lines)} строк")

    span_count = 0
    trace_count = 0
    for ln in lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError as exc:
            print(f"[smoke] FAIL: невалидный JSON: {ln[:100]!r} → {exc}")
            return 1
        kind = obj.get("kind")
        if kind == "span":
            span_count += 1
        elif kind == "trace":
            trace_count += 1
        else:
            print(f"[smoke] FAIL: неизвестный kind={kind!r}")
            return 1

    print(f"[smoke] spans: {span_count}, traces: {trace_count}")

    if trace_count != len(test_queries):
        print(f"[smoke] FAIL: ожидалось {len(test_queries)} trace, получено {trace_count}")
        return 1
    if span_count == 0:
        print("[smoke] FAIL: ни одного span не записано")
        return 1

    print("\n[smoke] sample line (first span):")
    print(f"  {lines[0]}")
    print("\n[smoke] sample line (last trace summary):")
    last_trace = next(
        (ln for ln in reversed(lines) if json.loads(ln).get("kind") == "trace"), None
    )
    if last_trace:
        print(f"  {last_trace}")

    print("\n[smoke] PASS")
    return 0


def main() -> int:
    _setup_logging()
    return asyncio.run(_smoke())


if __name__ == "__main__":
    sys.exit(main())
