"""Smoke: симулируем agent loop где **один user-request** дёргает
несколько tools, и проверяем что **все они в одном trace**.

Имитация: для каждого user-request создаём fake `ToolContext` с фиксированным
`task_id`, потом вызываем 3 tool handler'а с этим контекстом. Все 3 вызова
должны попасть в один trace в Langfuse / JSON-trace.

Запуск:
    PYTHONPATH=. python scripts/smoke_observability_one_trace.py
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import tempfile
from dataclasses import dataclass
from typing import Optional


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def _load_env() -> None:
    env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass
class FakeToolContext:
    """Минимальный stub для ouroboros ToolContext."""

    task_id: str
    current_chat_id: Optional[int] = None


def _smoke() -> int:
    tmp_run_dir = pathlib.Path(tempfile.mkdtemp(prefix="nefteboros_one_trace_"))
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

    # === Сценарий 1: один user-request → 3 tool вызова под одним task_id ===
    ctx_request_a = FakeToolContext(task_id="user_req_AAA", current_chat_id=42)
    print(f"\n[smoke] ── user-request A (task_id={ctx_request_a.task_id}) ──")

    print("[smoke] tool: rag_search")
    _tool_rag_search(ctx_request_a, query="OPEC квоты 2026", k=3)

    print("[smoke] tool: web_search")
    _tool_web_search(ctx_request_a, query="brent oil price today", k=3)

    print("[smoke] tool: analyst_query")
    _tool_analyst_query(ctx_request_a, query="прогноз brent на 3 месяца")

    # Финал user-request A — закрываем явно (имитация loop hook).
    tracer.close_trace_for_request(
        f"task:{ctx_request_a.task_id}",
        answer="Полный ответ агента на user-request A",
    )

    # === Сценарий 2: другой user-request → один tool ===
    ctx_request_b = FakeToolContext(task_id="user_req_BBB", current_chat_id=42)
    print(f"\n[smoke] ── user-request B (task_id={ctx_request_b.task_id}) ──")

    print("[smoke] tool: web_search")
    _tool_web_search(ctx_request_b, query="urals discount today")

    tracer.close_trace_for_request(f"task:{ctx_request_b.task_id}")

    # === Сценарий 3: tool без ctx (legacy) — отдельный trace ===
    print("\n[smoke] ── legacy call без ctx ──")
    _tool_rag_search(query="legacy fallback test")

    # Flush
    if tracer._langfuse_enabled and tracer._langfuse_client is not None:
        try:
            tracer._langfuse_client.flush()
            print("\n[smoke] flush ok")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[smoke] flush failed: {exc}")
            return 1

    # Анализ JSON-trace.
    trace_file = tmp_run_dir / "trace.jsonl"
    if not trace_file.exists():
        print(f"[smoke] FAIL: {trace_file} не создан")
        return 1

    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
    span_count = sum(1 for ln in lines if json.loads(ln).get("kind") == "span")
    trace_count = sum(1 for ln in lines if json.loads(ln).get("kind") == "trace")

    # Группируем span'ы по trace_id чтобы убедиться что request A — один trace.
    by_trace: dict[str, list[str]] = {}
    for ln in lines:
        obj = json.loads(ln)
        if obj.get("kind") == "span":
            tid = obj.get("trace_id", "")
            by_trace.setdefault(tid, []).append(obj.get("node", "?"))

    print(f"\n[smoke] JSON-trace: {len(lines)} строк "
          f"({span_count} spans, {trace_count} trace summaries, "
          f"{len(by_trace)} unique trace_ids в spans)")

    # Sanity: ожидаем
    #   trace A: 3 tool span + узлы графа (4) внутри analyst_query = 7
    #   trace B: 1 tool span = 1
    #   legacy:  1 tool span = 1 (отдельный trace, без ctx)
    # → 3 trace_id, 9 spans, 3 trace summaries.
    print(f"\n[smoke] spans by trace_id:")
    for tid, nodes in by_trace.items():
        print(f"  trace_id={tid[:12]}... spans={nodes}")

    if trace_count != 3:
        print(f"\n[smoke] FAIL: ожидалось 3 trace summary, получено {trace_count}")
        return 1
    if len(by_trace) != 3:
        print(f"\n[smoke] FAIL: ожидалось 3 unique trace_id, получено {len(by_trace)}")
        return 1

    # Главное — request A должен иметь все 3 tool span'а в одном trace.
    trace_a_nodes = max(by_trace.values(), key=len)  # самый «толстый» trace = A
    expected_a_tools = {"rag_search", "web_search", "analyst_query"}
    actual_a_tools = set(trace_a_nodes) & expected_a_tools
    if actual_a_tools != expected_a_tools:
        print(f"\n[smoke] FAIL: trace A не содержит все 3 tool — actual={actual_a_tools}")
        return 1

    print("\n[smoke] PASS — user-request A: 3 tools в ОДНОМ trace ✓")
    print("[smoke] Открой UI: https://cloud.langfuse.com/")
    return 0


def main() -> int:
    _setup_logging()
    _load_env()
    return _smoke()


if __name__ == "__main__":
    sys.exit(main())
