"""Track F verify по всем 3 типам tools (web/rag/forecast):
для каждого отправляем WS запрос → ждём ответ → читаем trace через Langfuse API
→ проверяем name/output(real)/total_cost>0/all generations с cost.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import websockets
from langfuse import Langfuse, get_client


QUERIES = [
    ("web_search",    "Какая текущая цена нефти Brent сегодня? Дай актуальное число."),
    ("rag_search",    "Что говорится в отчёте OPEC 2025 про квоты добычи участников картеля?"),
    ("analyst_query", "Сделай прогноз цены Brent на ближайшие 3 месяца. Используй методологию SARIMAX и покажи числа."),
]


async def send_one(label: str, text: str) -> None:
    msg_id = f"all_tools_{label}_{int(time.time())}"
    print(f"\n[ws] >>> [{label}] {text[:60]}...", flush=True)
    async with websockets.connect("ws://localhost:8000/ws", max_size=10*1024*1024) as ws:
        await ws.send(json.dumps({
            "type": "chat", "content": text,
            "sender_session_id": msg_id, "client_message_id": msg_id,
        }))
        t0 = time.time()
        last = 0
        while time.time() - t0 < 360:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
            except asyncio.TimeoutError:
                continue
            try:
                m = json.loads(raw)
            except Exception:
                continue
            if m.get("type") == "chat" and m.get("role") == "assistant":
                c = m.get("content", "") or ""
                if c and len(c) != last:
                    last = len(c)
                    if m.get("done") or len(c) > 200:
                        print(f"[ws]     [{label}] chars={len(c)} done={m.get('done')}", flush=True)
                        await asyncio.sleep(3)
                        return


async def main_async() -> None:
    for label, text in QUERIES:
        await send_one(label, text)
    print("\n[verify] all sent. flush + 30s ingest", flush=True)


def main() -> int:
    asyncio.run(main_async())
    get_client().flush()
    time.sleep(30)

    c = Langfuse()
    all_ts = c.api.trace.list(limit=50).data
    print("\n=== TRACES ===", flush=True)
    ok_all = True
    for label, q in QUERIES:
        marker = q[:25]
        matches = [t for t in all_ts if t.session_id == "chat:1" and marker in str(t.input or "")]
        matches = sorted(matches, key=lambda t: t.timestamp or 0, reverse=True)
        if not matches:
            print(f"\n[{label}] ✗ trace not found")
            ok_all = False
            continue
        t = matches[0]
        obs = c.api.legacy.observations_v1.get_many(trace_id=t.id, limit=100).data
        obs_names = sorted(set(o.name for o in obs))
        n_gen = sum(1 for o in obs if o.type == "GENERATION")
        n_gen_cost = sum(
            1 for o in obs if o.type == "GENERATION" and getattr(o, "calculated_total_cost", None)
        )
        output_str = str(t.output)
        is_real_output = "answer" in output_str and "events_count" not in output_str
        cost = getattr(t, "total_cost", None) or 0.0
        print(f"\n[{label}] {t.id[:16]}...")
        print(f"  name={t.name!r}, latency={getattr(t, 'latency', None)}s")
        print(f"  output: {output_str[:120]}")
        print(f"  total_cost={cost}, generations={n_gen}, with_cost={n_gen_cost}")
        print(f"  observations: {obs_names}")
        n_ok = t.name == "user_request"
        o_ok = is_real_output
        c_ok = cost > 0 and (n_gen == 0 or n_gen_cost == n_gen)
        e_ok = label in obs_names
        marks = (
            f"  {'✓' if n_ok else '✗'} name  "
            f"{'✓' if o_ok else '✗'} output(real)  "
            f"{'✓' if c_ok else '✗'} cost(all gens)  "
            f"{'✓' if e_ok else '✗'} tool({label}) present"
        )
        print(marks)
        if not (n_ok and o_ok and c_ok and e_ok):
            ok_all = False

    print("\n" + "=" * 60)
    print("VERDICT:", "PASS ✓" if ok_all else "FAIL ✗")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
