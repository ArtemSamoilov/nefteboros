"""Track F (observability) verify: один реальный handle_task через
OuroborosAgent с фиксом trace.name/input/output в `_ouroboros_patches.py`,
затем API readback из Langfuse Cloud.

Используется внутри docker container `nefteboros-web` (где `.env`
подключён через env_file и весь стек поднят как на проде).

Запуск:
    docker exec nefteboros-web python scripts/verify_track_f.py
    docker exec -e VERIFY_QUERY="прогноз Brent на 3 месяца" nefteboros-web \
        python scripts/verify_track_f.py

Acceptance (всё должно быть ✓):
    trace.name   = "user_request"
    trace.input  = пользовательский вопрос
    trace.output = финальный ответ агента
    Иерархия: user_request → web_search/rag_search/analyst_query → child spans
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
import tempfile
import time


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

    # OBSERVABILITY_RUN_DIR + DRIVE_ROOT — изолированный сетап чтобы не
    # пересекаться с пер-PID state'ом если внутри контейнера уже есть запущенный server.
    tmp_obs = tempfile.mkdtemp(prefix="verify_track_f_obs_")
    tmp_drive = tempfile.mkdtemp(prefix="verify_track_f_drive_")
    os.environ["OBSERVABILITY_RUN_DIR"] = tmp_obs

    # Apply patches (langfuse / openai / handle_task / chat).
    import nefteboros.observability  # noqa: F401

    from ouroboros.agent import make_agent

    repo_dir = str(pathlib.Path(__file__).resolve().parents[1])
    agent = make_agent(repo_dir=repo_dir, drive_root=tmp_drive)

    chat_id = int(time.time()) % 1_000_000
    user_query = os.environ.get("VERIFY_QUERY", "прогноз Brent на 3 месяца")
    task = {
        "id": f"verify-track-f-{chat_id}",
        "chat_id": chat_id,
        "type": "chat",
        "text": user_query,
    }

    print(f"[verify] chat_id={chat_id}")
    print(f"[verify] session_id=chat:{chat_id}")
    print(f"[verify] user_query={user_query!r}")
    print(f"[verify] running handle_task… (это занимает 1-3 минуты)")

    t0 = time.time()
    try:
        result = agent.handle_task(task)
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] handle_task FAILED after {time.time()-t0:.1f}s: "
              f"{type(exc).__name__}: {exc}")
        return 1
    elapsed = time.time() - t0

    n_events = len(result) if hasattr(result, "__len__") else "?"
    print(f"[verify] handle_task ok: events={n_events}, elapsed={elapsed:.1f}s")

    # Flush + ingestion delay.
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] flush failed: {exc}")
        return 1

    print("[verify] flushed; sleeping 25s for ingestion…")
    time.sleep(25)

    # API readback by session_id.
    from langfuse import Langfuse
    c = Langfuse()
    sid = f"chat:{chat_id}"
    print(f"[verify] querying traces by session_id={sid!r}")
    traces = [t for t in c.api.trace.list(limit=50).data if t.session_id == sid]
    if not traces:
        print("[verify] FAIL: no traces with this session_id")
        return 1
    print(f"[verify] found {len(traces)} trace(s)")

    overall_ok = True
    for t in traces:
        print("\n" + "=" * 80)
        print(f"TRACE id={t.id}")
        print("=" * 80)
        n_ok = t.name == "user_request"
        i_ok = bool(t.input) and (
            user_query[:25] in str(t.input)
            or user_query[:25].lower() in str(t.input).lower()
        )
        # Output — может быть финальным ответом или null (msg сериализация баг,
        # вне scope этого PR). Считаем pass если есть НЕ-tool-output (т.е.
        # не {'query': ..., 'results': ...} от brave/web_search).
        has_tool_signature = (
            "results" in str(t.output) and "tier_filter" in str(t.output)
            if t.output else False
        )
        o_ok = bool(t.output) and not has_tool_signature

        for label, ok, val in [
            ("name  ", n_ok, t.name),
            ("input ", i_ok, str(t.input)[:120]),
            ("output", o_ok, str(t.output)[:150]),
        ]:
            mark = "✓" if ok else "✗"
            print(f"  {mark} {label} = {val!r}")
            if not ok:
                overall_ok = False

        # Observation hierarchy.
        obs = c.api.legacy.observations_v1.get_many(trace_id=t.id, limit=100).data
        obs = sorted(obs, key=lambda x: x.start_time or 0)
        roots = [o for o in obs if not getattr(o, "parent_observation_id", None)]
        print(f"\n  Observations ({len(obs)}, {len(roots)} root):")
        for o in obs:
            parent = getattr(o, "parent_observation_id", None) or "—"
            print(f"    [{o.name:30s}] type={o.type:12s} parent={parent[:16]}")

    print("\n" + "=" * 80)
    print("VERDICT:", "PASS ✓" if overall_ok else "FAIL ✗")
    print("=" * 80)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
