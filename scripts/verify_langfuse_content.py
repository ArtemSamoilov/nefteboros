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
    """Прогон 3 user-request'ов под одной session.

    Имитирует Ouroboros agent loop: каждый user-request это отдельный task,
    и в task происходит:
      1. Tool dispatch (rag_search / web_search / analyst_query).
      2. Финальный синтез агента из tool result через LLMClient.chat_async.

    На проде это всё делает `OuroborosAgent.handle_task` (наш patch
    оборачивает в root span). Здесь имитируем явно через
    `start_as_current_observation` + `propagate_attributes`.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="nefteboros_verify_"))
    os.environ["OBSERVABILITY_RUN_DIR"] = str(tmp)

    import asyncio

    from langfuse import get_client, propagate_attributes
    from skills.neftegaz_analyst.plugin import (
        _tool_analyst_query,
        _tool_rag_search,
        _tool_web_search,
    )

    ts = int(time.time())
    sid = f"chat:verify_chat_{ts}"
    user_id = "artem-verify"
    print(f"[verify] session_id={sid}")

    lf = get_client()

    async def _agent_synthesize_after_tool(
        tool_result_json: str, query: str
    ) -> str:
        """Имитирует синтез финального ответа Ouroboros'ом после tool вернул
        данные. Через `LLMClient.chat_async` (patched наш wrap → ouroboros_chat
        generation в trace). Возвращает текст ответа."""
        try:
            from ouroboros.llm import LLMClient

            client = LLMClient()
            messages = [
                {
                    "role": "system",
                    "content": "Ты аналитик нефтегазового рынка. Кратко синтезируй ответ.",
                },
                {
                    "role": "user",
                    "content": f"Запрос: {query}\n\nДанные tool: {tool_result_json[:2000]}",
                },
            ]
            # max_tokens НЕ задаём — global default 256K (см. ADR-0021).
                # Kimi-k2p6 тратит часть output на скрытый reasoning_content;
                # с малым max_tokens видимый content приходит пустым.
            msg, _usage = await client.chat_async(
                messages=messages,
                model="openai-compatible::kimi-k2p6",
            )
            return (msg or {}).get("content") or ""
        except Exception as exc:  # noqa: BLE001
            print(f"  [synthesize] failed: {type(exc).__name__}: {exc}")
            return ""

    def _user_request(task_label: str, tool_call, synthesize_after: bool = True):
        """Один user-request: root user_request span + propagate + tool +
        опциональный synthesize. Имитирует Ouroboros handle_task.

        synthesize_after:
            True (rag, web) — после tool делаем агентский LLM-синтез из
                JSON-данных (имитация core loop).
            False (analyst) — внутри analyst_query graph subgraph уже есть
                synthesize узел который сам генерит markdown. Дополнительный
                fake-synthesize создаёт лишний span и переписывает trace.io.
        """
        ctx = FakeCtx(
            task_id=f"verify_{ts}_{task_label}",
            current_chat_id=f"verify_chat_{ts}",
            user_id=user_id,
        )
        print(f"\n[verify] user-request: {task_label}")
        with lf.start_as_current_observation(
            name="user_request",
            as_type="agent",
            input={"task": task_label, "query": None},  # query задаётся в tool_call
        ) as root_span:
            with propagate_attributes(
                session_id=sid, user_id=user_id, trace_name="user_request"
            ):
                tool_result, query = tool_call(ctx)
                root_span.update(input={"task": task_label, "query": query})
                if synthesize_after:
                    final_answer = asyncio.run(
                        _agent_synthesize_after_tool(tool_result, query)
                    )
                else:
                    # Для analyst tool сам уже синтезировал в `tool_result`
                    # (graph узел synthesize). Парсим его и берём synthesis.
                    try:
                        parsed = json.loads(tool_result) if isinstance(
                            tool_result, str
                        ) else tool_result
                        final_answer = (
                            parsed.get("synthesis", "")
                            if isinstance(parsed, dict)
                            else str(tool_result)
                        )
                    except Exception:  # noqa: BLE001
                        final_answer = str(tool_result)[:1000]
                output_payload = {"answer": final_answer}
                root_span.update(output=output_payload)

    def _call_rag(ctx):
        q = "OPEC квоты добычи нефти 2026"
        return _tool_rag_search(ctx, query=q, k=3), q

    def _call_web(ctx):
        q = "brent oil price today"
        return _tool_web_search(ctx, query=q, k=3), q

    def _call_analyst(ctx):
        q = "прогноз brent на 3 месяца"
        return _tool_analyst_query(ctx, query=q), q

    _user_request("rag", _call_rag)
    _user_request("web", _call_web)
    _user_request("analyst", _call_analyst, synthesize_after=False)

    lf.flush()
    print("\n[verify] flush ok, waiting 15s for ingestion…")
    time.sleep(15)
    return sid


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
