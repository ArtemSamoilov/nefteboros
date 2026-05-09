"""Прогнать smoke + прочитать traces из Langfuse API → проверить content.

Делает:
1. 3 tool вызова (`rag_search`, `web_search`, `analyst_query`) под одним
   `FakeToolContext` (имитация Ouroboros agent loop).
2. Через 12s ингеста — лист traces по `session_id` filter.
3. Печатает каждый trace + observations с full content (input/output).

Ожидание:
- 3 traces, у всех `name='user_request'`, `session_id='chat:<test>'`
  в **native** поле, `user_id` опционально.
- analyst_query trace содержит 5 observations (root + classify_intent +
  forecast_call + synthesize + validate_citations) с правильным parent.

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
    current_chat_id: Optional[str] = None
    user_id: Optional[str] = None


def _short(value, n=300):
    s = (
        json.dumps(value, ensure_ascii=False, default=str)
        if not isinstance(value, str)
        else value
    )
    return s if len(s) <= n else s[:n] + f"… [+{len(s)-n} chars]"


def _smoke() -> str:
    """Прогон 3 tool вызовов под одним FakeCtx. Возвращает session_id."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="nefteboros_verify_"))
    os.environ["OBSERVABILITY_RUN_DIR"] = str(tmp)

    from skills.neftegaz_analyst.plugin import (
        _tool_analyst_query,
        _tool_rag_search,
        _tool_web_search,
    )

    ts = int(time.time())
    ctx = FakeCtx(
        task_id=f"verify_{ts}",
        current_chat_id=f"verify_chat_{ts}",
        user_id="artem-verify",
    )
    print(f"[verify] task_id={ctx.task_id} chat={ctx.current_chat_id}")

    print("[verify] tool: rag_search …")
    _tool_rag_search(ctx, query="OPEC квоты добычи нефти 2026", k=3)

    print("[verify] tool: web_search …")
    _tool_web_search(ctx, query="brent oil price today", k=3)

    print("[verify] tool: analyst_query …")
    _tool_analyst_query(ctx, query="прогноз brent на 3 месяца")

    try:
        from langfuse import get_client

        get_client().flush()
        print("[verify] flush ok, waiting 15s for ingestion…")
        time.sleep(15)
    except ImportError:
        print("[verify] langfuse SDK не установлен — JSON-trace only")

    return f"chat:{ctx.current_chat_id}"


def _print_trace(client, t) -> None:
    print("\n" + "=" * 80)
    print(f"TRACE  id={t.id}")
    print("=" * 80)
    print(f"  name        : {t.name!r}")
    print(f"  session_id  : {t.session_id!r}")
    print(f"  user_id     : {t.user_id!r}")
    print(f"  input       : {_short(t.input, 200)}")
    print(f"  output      : {_short(t.output, 400)}")

    obs = client.api.legacy.observations_v1.get_many(trace_id=t.id, limit=100).data
    obs = sorted(obs, key=lambda x: x.start_time or 0)
    print(f"\n  Observations ({len(obs)}):")
    for o in obs:
        parent = getattr(o, "parent_observation_id", None)
        usage = getattr(o, "usage", None)
        cost = getattr(o, "calculated_total_cost", None)
        model = getattr(o, "model", None)
        print(f"\n    [{o.name}]  type={o.type}  parent={parent}")
        if model:
            print(f"      model       : {model}")
        if usage:
            print(f"      usage       : {_short(usage, 200)}")
        if cost:
            print(f"      cost        : {cost}")
        print(f"      input       : {_short(o.input, 250)}")
        print(f"      output      : {_short(o.output, 500)}")


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING, format="%(name)s %(levelname)s %(message)s"
    )
    _load_env()
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

    session_id = _smoke()

    from langfuse import Langfuse

    c = Langfuse()
    print(f"\n[verify] looking up traces for session_id={session_id!r} (native field)")
    all_traces = c.api.trace.list(limit=50).data
    matching = [t for t in all_traces if t.session_id == session_id]
    print(f"[verify] found {len(matching)} traces with this session_id\n")

    if not matching:
        print("[verify] FAIL: no traces found, что-то сломалось")
        return 1

    for t in matching:
        _print_trace(c, t)

    # Проверка ожиданий
    names = [t.name for t in matching]
    sessions = [t.session_id for t in matching]
    print("\n" + "=" * 80)
    print("ASSERTIONS")
    print("=" * 80)
    ok = True
    for t in matching:
        if t.name != "user_request":
            print(f"  FAIL: trace {t.id} имеет name={t.name!r}, ожидалось 'user_request'")
            ok = False
        if t.session_id != session_id:
            print(f"  FAIL: trace {t.id} имеет session_id={t.session_id!r}")
            ok = False
    if ok:
        print(f"  ✓ All {len(matching)} traces имеют name='user_request' и session_id={session_id!r}")

    # analyst_query trace должен иметь >=5 observations (root + 4 graph узла)
    found_with_5_obs = False
    for t in matching:
        obs = c.api.legacy.observations_v1.get_many(trace_id=t.id).data
        if len(obs) >= 5:
            found_with_5_obs = True
            print(f"  ✓ Trace {t.id} имеет {len(obs)} observations (analyst_query с graph узлами)")
            break
    if not found_with_5_obs:
        print("  FAIL: ни один trace не имеет >=5 observations (analyst_query graph узлы потерялись?)")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
