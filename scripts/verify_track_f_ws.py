"""Track F (observability) verify через WS endpoint server'а — prod-parity путь.

Вместо direct handle_task с tempfile drive (где skill neftegaz_analyst НЕ
зарегистрирован → агент без tools), отправляем 3 запроса через WebSocket
живого сервера в контейнере. Server.py использует /app/data drive где
skill-state уже на месте, значит web_search/rag_search/analyst_query
доступны агенту.

3 запроса, каждый сформулирован чтобы спровоцировать конкретный tool:
    1. web_search   — текущая цена / новости (фактологический online data).
    2. rag_search   — документная база (OPEC отчёты).
    3. analyst_query — forecast graph (SARIMAX/прогноз).

После — Langfuse API readback: для каждого trace проверяем
trace.name='user_request' + находим observations нужного типа в hierarchy.

Запуск (с хоста, контейнер должен быть up):
    python scripts/verify_track_f_ws.py
Или внутри контейнера:
    docker exec nefteboros-web python scripts/verify_track_f_ws.py
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time
from typing import Any, Optional


SERVER_URL = os.environ.get("VERIFY_WS_URL", "ws://localhost:8000/ws")
INGEST_DELAY = int(os.environ.get("VERIFY_INGEST_DELAY", "30"))

# Запросы — каждый явно нацелен на конкретный tool. Текст не должен быть
# слишком длинным (LLM thinking time), но должен быть однозначным triggеr.
QUERIES = [
    {
        "label": "web_search",
        "text": "Какая текущая цена нефти Brent сегодня? Дай актуальное число.",
        "expected_observation": "web_search",
    },
    {
        "label": "rag_search",
        "text": "Что говорится в отчёте OPEC 2025 про квоты добычи участников картеля?",
        "expected_observation": "rag_search",
    },
    {
        "label": "analyst_query",
        "text": "Сделай прогноз цены Brent на ближайшие 3 месяца. Используй методологию SARIMAX и покажи числа.",
        "expected_observation": "analyst_query",
    },
]


async def _send_one(query: dict[str, Any], wait_seconds: int = 240) -> None:
    """Подключиться, отправить chat сообщение, дождать assistant reply."""
    import websockets

    label = query["label"]
    text = query["text"]
    print(f"\n[ws] >>> {label}: {text!r}")

    msg_id = f"verify_{label}_{int(time.time())}"
    sender_id = f"verify_track_f_{int(time.time())}"

    async with websockets.connect(SERVER_URL, max_size=10 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "type": "chat",
            "content": text,
            "sender_session_id": sender_id,
            "client_message_id": msg_id,
        }))

        # Слушаем сообщения от сервера. Завершаем по получению assistant reply
        # (role='assistant', content!=пустой) или по таймауту.
        t0 = time.time()
        last_role = None
        last_chars = 0
        while time.time() - t0 < wait_seconds:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "chat":
                role = msg.get("role")
                content = msg.get("content", "") or ""
                if role and role != last_role:
                    last_role = role
                if role == "assistant" and content:
                    chars = len(content)
                    if chars != last_chars:
                        last_chars = chars
                        print(f"[ws]     [{label}] assistant chars={chars}")
                    if msg.get("done") or chars > 50:
                        # Получили существенный ответ — даём ещё пару секунд
                        # на финальные events и уходим.
                        await asyncio.sleep(2.0)
                        return
        print(f"[ws]     [{label}] timeout after {wait_seconds}s (response not finalised)")


async def _send_all() -> None:
    for q in QUERIES:
        await _send_one(q, wait_seconds=240)


def _check_traces() -> int:
    """API readback: filter by session=chat:1 + time > start, для каждого
    проверить name='user_request' и наличие нужного observation."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from langfuse import Langfuse

    c = Langfuse()
    # Берём последние 30 trace'ов из session chat:1 (web bridge default).
    all_traces = c.api.trace.list(limit=50).data
    sess_traces = [t for t in all_traces if t.session_id == "chat:1"]
    sess_traces = sorted(sess_traces, key=lambda t: t.timestamp or 0, reverse=True)
    if not sess_traces:
        print("[verify] FAIL: нет traces в session chat:1")
        return 1

    # Сматчиваем 3 запроса с recent traces по тексту user_query'а.
    overall_ok = True
    print(f"\n[verify] последних traces в chat:1: {len(sess_traces)} (показываем 5)")
    for t in sess_traces[:5]:
        print(f"  - {t.id[:16]}  name={t.name!r}  input={str(t.input)[:80]}")

    print("\n[verify] === ASSERTIONS ===")
    for q in QUERIES:
        marker = q["text"][:25]
        match = next(
            (t for t in sess_traces if marker in str(t.input or "")),
            None,
        )
        if match is None:
            print(f"\n  [{q['label']:14s}] ✗ trace not found (marker={marker!r})")
            overall_ok = False
            continue
        # name check
        n_ok = match.name == "user_request"
        # input check (presence of marker)
        i_ok = marker in str(match.input or "")
        # observation check
        obs = c.api.legacy.observations_v1.get_many(trace_id=match.id, limit=100).data
        obs_names = [o.name for o in obs]
        e_ok = q["expected_observation"] in obs_names

        print(
            f"\n  [{q['label']:14s}] trace={match.id[:16]}…\n"
            f"    {'✓' if n_ok else '✗'} name = {match.name!r}\n"
            f"    {'✓' if i_ok else '✗'} input contains {marker!r}\n"
            f"    {'✓' if e_ok else '✗'} observation {q['expected_observation']!r} present "
            f"(observations: {sorted(set(obs_names))})"
        )
        if not (n_ok and i_ok and e_ok):
            overall_ok = False

    print("\n" + "=" * 80)
    print("VERDICT:", "PASS ✓" if overall_ok else "FAIL ✗")
    print("=" * 80)
    return 0 if overall_ok else 1


def main() -> int:
    print(f"[verify] WS endpoint: {SERVER_URL}")
    print(f"[verify] queries: {len(QUERIES)}")
    asyncio.run(_send_all())
    print(f"\n[verify] all sent. Sleeping {INGEST_DELAY}s for ingestion…")
    time.sleep(INGEST_DELAY)
    return _check_traces()


if __name__ == "__main__":
    sys.exit(main())
