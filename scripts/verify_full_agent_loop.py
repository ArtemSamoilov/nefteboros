"""Verify полного agent loop: симуляция Ouroboros handle_task → финальный
ответ агента (синтез после tool вызова) виден в Langfuse trace.

В отличие от `verify_langfuse_content.py` (который вызывает tool handlers
напрямую), этот скрипт вызывает `OuroborosAgent.handle_task(task)` —
полный agent loop с LLM thinking, tool dispatching, и финальным synthesize.

Ожидание: в Langfuse traces появятся `openai`-generation observations от
langfuse.openai instrumentor — это и есть синтез финального ответа агента
после того как rag_search / web_search вернули данные.

Запуск:
    PYTHONPATH=. python scripts/verify_full_agent_loop.py
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import tempfile
import time


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


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    _load_env()
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="nefteboros_full_loop_"))
    os.environ["OBSERVABILITY_RUN_DIR"] = str(tmp)

    # Импорт observability ДО agent — чтобы patches применились.
    import nefteboros.observability  # noqa: F401  (apply_patches at import)

    from ouroboros.agent import Env, make_agent

    repo_dir = str(pathlib.Path(__file__).resolve().parents[1])
    drive_root = tempfile.mkdtemp(prefix="nefteboros_drive_")

    print(f"[verify-full] repo_dir={repo_dir}")
    print(f"[verify-full] drive_root={drive_root}")

    agent = make_agent(repo_dir=repo_dir, drive_root=drive_root)

    chat_id = int(time.time()) % 1_000_000
    task = {
        "id": f"full-loop-{chat_id}",
        "chat_id": chat_id,
        "type": "chat",
        "text": "Что говорит OPEC про квоты добычи нефти в 2026?",
    }

    print(f"\n[verify-full] handle_task: chat_id={chat_id}")
    print(f"[verify-full] question: {task['text']!r}")
    try:
        result = agent.handle_task(task)
        print(f"[verify-full] handle_task returned (len={len(result) if hasattr(result, '__len__') else '?'})")
    except Exception as exc:  # noqa: BLE001
        print(f"[verify-full] handle_task FAILED: {type(exc).__name__}: {exc}")
        return 1

    # Flush + wait ingestion
    try:
        from langfuse import get_client

        get_client().flush()
        print("[verify-full] flush ok, waiting 20s for ingestion…")
        time.sleep(20)
    except ImportError:
        print("[verify-full] langfuse SDK not installed")
        return 1

    # Найдём trace по session_id
    from langfuse import Langfuse

    c = Langfuse()
    sid = f"chat:{chat_id}"
    print(f"[verify-full] looking up traces with session_id={sid!r}")
    traces = [t for t in c.api.trace.list(limit=50).data if t.session_id == sid]
    print(f"[verify-full] found {len(traces)} traces")

    for t in traces:
        print(f"\n--- Trace [{t.name}] id={t.id} ---")
        obs = c.api.legacy.observations_v1.get_many(trace_id=t.id).data
        print(f"  observations: {len(obs)}")
        for o in sorted(obs, key=lambda x: x.start_time or 0):
            preview_out = (str(o.output)[:120] if o.output else "")
            print(f"    [{o.name}] type={o.type}  out={preview_out}")

    return 0 if traces else 1


if __name__ == "__main__":
    sys.exit(main())
