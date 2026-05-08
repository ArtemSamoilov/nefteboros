"""Smoke с Langfuse Cloud — проверка что трейсы доходят до UI.

Запуск:
    # с .env (LANGFUSE_ENABLED=true + ключи)
    python scripts/smoke_observability_langfuse.py

Ожидание:
    - 3 диалога прогоняются через analyst_graph.
    - Trace (root) + 5 child observations на запрос видны в Langfuse UI:
      https://cloud.langfuse.com/project/<project-id>/traces
    - Узлы synthesize / llm_disambiguate отрисовываются как generation
      (с model / usage / cost), остальные — как span.
    - Локально пишется trace.jsonl как backup.
"""

from __future__ import annotations

import asyncio
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
    """Загрузить .env (worktree-local) перед стартом."""
    env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        print(f"[smoke] .env не найден по пути {env_path}")
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
    print(f"[smoke] .env загружен из {env_path}")


async def _smoke() -> int:
    tmp_run_dir = pathlib.Path(tempfile.mkdtemp(prefix="nefteboros_lf_smoke_"))
    os.environ["OBSERVABILITY_RUN_DIR"] = str(tmp_run_dir)
    print(f"[smoke] OBSERVABILITY_RUN_DIR = {tmp_run_dir}")
    print(f"[smoke] LANGFUSE_ENABLED = {os.environ.get('LANGFUSE_ENABLED')}")
    print(f"[smoke] LANGFUSE_HOST    = {os.environ.get('LANGFUSE_HOST', '<default>')}")

    from nefteboros.graphs.analyst_graph import build_analyst_graph, invoke_with_trace
    from nefteboros.graphs.state import GraphState
    from nefteboros.observability.tracer import get_tracer

    tracer = get_tracer()
    if not tracer._langfuse_enabled:
        print("[smoke] WARNING: Langfuse не enabled — продолжим (JSON-trace only).")

    graph = build_analyst_graph()

    test_queries = [
        "прогноз brent на 3 месяца",
        "что такое OPEC+",
        "цены на российский газ",
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

    # Финальный flush.
    if tracer._langfuse_enabled and tracer._langfuse_client is not None:
        print("\n[smoke] flush Langfuse...")
        try:
            tracer._langfuse_client.flush()
            print("[smoke] flush ok")
        except Exception as exc:  # noqa: BLE001
            print(f"[smoke] flush failed: {exc}")
            return 1

    trace_file = tmp_run_dir / "trace.jsonl"
    if trace_file.exists():
        lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
        print(f"\n[smoke] JSON-trace: {len(lines)} строк в {trace_file}")
    else:
        print(f"\n[smoke] WARNING: {trace_file} не создан")
        return 1

    print("\n[smoke] PASS — проверь UI: https://cloud.langfuse.com/")
    return 0


def main() -> int:
    _setup_logging()
    _load_env_file()
    return asyncio.run(_smoke())


if __name__ == "__main__":
    sys.exit(main())
