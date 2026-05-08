"""Прогнать smoke + прочитать traces из Langfuse API → показать что
действительно лежит в UI.

Цель: не верить write'у, а **прочитать обратно** через `client.api.trace.get`
и `client.api.observations.get_many` и распечатать содержание каждого
observation.

Запуск:
    PYTHONPATH=. python scripts/verify_langfuse_content.py
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


def _load_env() -> None:
    p = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if not p.exists():
        return
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, _, v = ln.partition("=")
            v = v.strip().strip('"').strip("'")
            if k and k.strip() not in os.environ:
                os.environ[k.strip()] = v


@dataclass
class FakeCtx:
    task_id: str
    current_chat_id: Optional[int] = 42


def _truncate(value, n=300):
    s = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return s if len(s) <= n else s[:n] + f"… [+{len(s)-n} chars]"


def _smoke_and_get_trace_id() -> tuple[str, str]:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="nefteboros_verify_"))
    os.environ["OBSERVABILITY_RUN_DIR"] = str(tmp)

    from nefteboros.observability.tracer import get_tracer
    from skills.neftegaz_analyst.plugin import (
        _tool_analyst_query,
        _tool_rag_search,
        _tool_web_search,
    )

    tracer = get_tracer()
    ts = int(time.time())
    ctx = FakeCtx(task_id=f"verify_{ts}", current_chat_id=f"verify_chat_{ts}")
    print(f"[verify] task_id = {ctx.task_id} chat = {ctx.current_chat_id}")

    # Реальные tool вызовы, как агент в Ouroboros loop'е
    print("[verify] tool: rag_search …")
    _tool_rag_search(ctx, query="OPEC квоты добычи нефти 2026", k=3)

    print("[verify] tool: web_search …")
    _tool_web_search(ctx, query="brent oil price today", k=3)

    print("[verify] tool: analyst_query …")
    _tool_analyst_query(ctx, query="прогноз brent на 3 месяца")

    # Захватим trace_id ДО закрытия (close_trace удалит его из registry).
    request_id = f"task:{ctx.task_id}"
    trace_obj = tracer._request_traces.get(request_id)
    if trace_obj is None:
        raise RuntimeError("trace для task не найден в registry")
    trace_id = trace_obj.trace_id
    print(f"[verify] trace_id = {trace_id}")

    tracer.close_trace_for_request(request_id, answer_full={
        "note": "это финал из smoke (сгенерирован скриптом, не настоящим ответом агента)",
    })
    # flush через native Langfuse SDK
    try:
        from langfuse import get_client
        get_client().flush()
        print("[verify] flush ok, waiting 12s for ingestion…")
        time.sleep(12)
    except Exception as exc:
        print(f"[verify] flush failed: {exc}")
    return trace_id, f"chat:{ctx.current_chat_id}"


def _print_trace_back(trace_id: str) -> int:
    from langfuse import Langfuse
    c = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )

    try:
        t = c.api.trace.get(trace_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] FAIL: trace.get({trace_id}): {type(exc).__name__}: {exc}")
        return 1

    print("\n" + "=" * 80)
    print(f"TRACE  id={trace_id}")
    print("=" * 80)
    print(f"  name        : {getattr(t, 'name', None)!r}")
    print(f"  session_id  : {getattr(t, 'session_id', None)!r}")
    print(f"  user_id     : {getattr(t, 'user_id', None)!r}")
    print(f"  metadata    : {_truncate(getattr(t, 'metadata', None))}")
    inp = getattr(t, "input", None)
    out = getattr(t, "output", None)
    print(f"  input       : {_truncate(inp)}")
    print(f"  output      : {_truncate(out)}")

    # v2 endpoint observations НЕ возвращает input/output (только metadata) —
    # используем legacy v1 для полного content. UI отображает то же что v1.
    obs_resp = c.api.legacy.observations_v1.get_many(trace_id=trace_id, limit=100)
    obs_list = sorted(obs_resp.data, key=lambda x: getattr(x, "start_time", 0) or 0)
    print(f"\n  Observations ({len(obs_list)}):")
    for o in obs_list:
        name = getattr(o, "name", None)
        otype = getattr(o, "type", None)
        oin = getattr(o, "input", None)
        oout = getattr(o, "output", None)
        usage = getattr(o, "usage_details", None) or getattr(o, "usage", None)
        cost = getattr(o, "cost_details", None) or getattr(o, "calculated_total_cost", None)
        model = getattr(o, "model", None)
        parent = getattr(o, "parent_observation_id", None)
        print()
        print(f"    [{name}]  type={otype}  parent={parent}")
        if model:
            print(f"      model       : {model}")
        if usage:
            print(f"      usage       : {_truncate(usage, 200)}")
        if cost:
            print(f"      cost        : {_truncate(cost, 200)}")
        print(f"      input       : {_truncate(oin, 400)}")
        print(f"      output      : {_truncate(oout, 600)}")

    return 0


def _list_traces_by_session(session_id: str) -> list[str]:
    """Найти все trace_ids недавно созданные с указанным session_id (через
    metadata filter — у нас session_id попадает в metadata, не в trace.session_id
    в SDK 4.x)."""
    from langfuse import Langfuse
    c = Langfuse()
    try:
        # Filter по metadata.session_id (поскольку set_session_id в 4.x не работает).
        all_traces = c.api.trace.list(limit=50)
        ids = []
        for t in all_traces.data:
            md = getattr(t, "metadata", None) or {}
            if isinstance(md, dict) and md.get("session_id") == session_id:
                ids.append(t.id)
        return ids
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] list traces failed: {exc}")
        return []


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")
    _load_env()
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

    trace_id, session_id = _smoke_and_get_trace_id()

    # Найдём все traces одной session — должно быть 3 (rag, web, analyst_query).
    print(f"\n[verify] looking up traces for session_id = {session_id!r}...")
    trace_ids = _list_traces_by_session(session_id)
    print(f"[verify] found {len(trace_ids)} traces with this session\n")

    if not trace_ids:
        # fallback на сохранённый trace_id
        trace_ids = [trace_id]

    rc = 0
    for tid in trace_ids:
        rc = max(rc, _print_trace_back(tid))
    return rc


if __name__ == "__main__":
    sys.exit(main())
