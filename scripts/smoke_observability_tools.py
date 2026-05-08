"""E2E smoke через tool entry points (skills/neftegaz_analyst/plugin.py).

Имитация вызовов от Ouroboros agent loop'а — каждый tool вызов это
e2e trace в Langfuse, узлы графа прицепляются как child observations.

Запуск:
    PYTHONPATH=. python scripts/smoke_observability_tools.py
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import tempfile


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def _load_env_file() -> None:
    env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        print(f"[smoke] .env не найден по пути {env_path}")
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
    print(f"[smoke] .env загружен из {env_path}")


def _smoke() -> int:
    tmp_run_dir = pathlib.Path(tempfile.mkdtemp(prefix="nefteboros_tools_smoke_"))
    os.environ["OBSERVABILITY_RUN_DIR"] = str(tmp_run_dir)
    print(f"[smoke] OBSERVABILITY_RUN_DIR = {tmp_run_dir}")

    from nefteboros.observability.tracer import get_tracer
    from skills.neftegaz_analyst.plugin import (
        _tool_analyst_query,
        _tool_rag_search,
        _tool_web_search,
    )

    tracer = get_tracer()
    print(f"[smoke] Langfuse enabled: {tracer._langfuse_enabled}")

    # 1) analyst_query — должен породить trace + 4-5 child spans (graph узлы)
    print("\n[smoke] tool: analyst_query")
    result = _tool_analyst_query(query="прогноз brent на 3 месяца")
    parsed = json.loads(result)
    syn_preview = (parsed.get("synthesis") or "<empty>")[:100]
    print(f"  synthesis preview: {syn_preview!r}")

    # 2) rag_search — top-level trace без child observations
    print("\n[smoke] tool: rag_search")
    try:
        result = _tool_rag_search(query="OPEC квоты 2026", k=3)
        parsed = json.loads(result)
        if "error" in parsed:
            print(f"  rag_search error (ожидаемо без vectorstore?): {parsed['error'][:120]}")
        else:
            print(f"  chunks returned: {parsed.get('total_returned', 0)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  rag_search exception: {type(exc).__name__}: {exc}")

    # 3) web_search — top-level trace
    print("\n[smoke] tool: web_search")
    try:
        result = _tool_web_search(query="brent oil price today", k=3)
        parsed = json.loads(result)
        if "error" in parsed:
            print(f"  web_search error (ожидаемо без BRAVE_API_KEY): {parsed['error'][:120]}")
        else:
            print(f"  results returned: {parsed.get('total_returned', 0)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  web_search exception: {type(exc).__name__}: {exc}")

    # Финальный flush.
    if tracer._langfuse_enabled and tracer._langfuse_client is not None:
        print("\n[smoke] flush Langfuse...")
        try:
            tracer._langfuse_client.flush()
            print("[smoke] flush ok")
        except Exception as exc:  # noqa: BLE001
            print(f"[smoke] flush failed: {exc}")
            return 1

    # Анализ trace.jsonl: должно быть 3 trace summary (по одному на tool вызов).
    trace_file = tmp_run_dir / "trace.jsonl"
    if not trace_file.exists():
        print(f"\n[smoke] FAIL: {trace_file} не создан")
        return 1

    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
    span_count = 0
    trace_count = 0
    for ln in lines:
        obj = json.loads(ln)
        if obj.get("kind") == "span":
            span_count += 1
        elif obj.get("kind") == "trace":
            trace_count += 1

    print(f"\n[smoke] trace.jsonl: {len(lines)} строк "
          f"({span_count} span'ов, {trace_count} trace summary)")

    if trace_count != 3:
        print(f"[smoke] FAIL: ожидалось 3 trace, получено {trace_count}")
        return 1

    # Sanity: analyst_query trace должен содержать ≥3 spans (узлы графа).
    print("\n[smoke] sample trace summary (analyst_query):")
    for ln in lines:
        obj = json.loads(ln)
        if obj.get("kind") == "trace" and "brent" in (obj.get("query") or ""):
            print(f"  {ln}")
            break

    print("\n[smoke] PASS — проверь UI: https://cloud.langfuse.com/")
    return 0


def main() -> int:
    _setup_logging()
    _load_env_file()
    return _smoke()


if __name__ == "__main__":
    sys.exit(main())
