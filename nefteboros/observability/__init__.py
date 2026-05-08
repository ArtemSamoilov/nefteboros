"""Observability — Langfuse трейсинг + JSONL backup для LangGraph узлов.

См. ADR-0024 (`docs/adr/0024-observability-langfuse.md`).

Использование:

    from nefteboros.observability import observe, log_llm_usage, start_trace, end_trace

    # В сборке графа (analyst_graph.py):
    builder.add_node("synthesize", observe(name="synthesize")(synthesize))

    # Внутри LLM-узла после chat-call:
    msg, usage = await client.chat_async(...)
    log_llm_usage(usage)

    # На уровне точки входа (CLI / tool entry):
    trace = start_trace(query=user_query)
    try:
        result = await graph.ainvoke({"query": user_query})
    finally:
        end_trace(trace, answer=result.get("synthesis"))

Errors никогда не попадают в финальный ответ агента или UI пользователя —
только в Python logging. Если Langfuse недоступен — JSON-trace продолжает
писаться в `metrics/runs/<ts>/trace.jsonl`.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Awaitable, Callable, Optional, TypeVar

from nefteboros.observability.tracer import (
    Span,
    Trace,
    _current_span,
    _current_trace,
    get_tracer,
    log_llm_usage,
)

logger = logging.getLogger(__name__)


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def observe(*, name: Optional[str] = None) -> Callable[[F], F]:
    """Декоратор / wrapper для async-узлов LangGraph.

    Применяется через wrap при `add_node` в `analyst_graph.py`, чтобы файлы
    `nefteboros/graphs/nodes/*.py` оставались чистыми (см. ADR-0024 §«Где
    лежат декораторы»).

    Поведение:
    - Перед вызовом — start_span(node=name).
    - Контекст span'а доступен через `_current_span.get()` для `log_llm_usage`.
    - После вызова — end_span(status="ok"|"error", output, error).
    - Узел не падает — exception ре-райзится наружу (LangGraph сам обработает).
    - Если top-level trace не открыт (graph вызван без `start_trace`) —
      span создастся, но привязки к trace не будет; warning в logger.

    Args:
        name: имя узла в трейсе. Если None — берётся `fn.__name__`.
    """

    def decorator(fn: F) -> F:
        node_name = name or fn.__name__

        if not inspect.iscoroutinefunction(fn):
            raise TypeError(
                f"observe(name={node_name!r}) применён к sync-функции; "
                "узлы LangGraph в analyst_graph должны быть async."
            )

        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            trace = _current_trace.get()

            if trace is None:
                # Узел вызван без обрамляющего start_trace (unit-test, прямой
                # вызов в дебаге). Создаём «orphan» trace — span запишется,
                # но без summary-строки и без агрегации.
                #
                # В production entry-points (CLI, ouroboros tool, eval) trace
                # открывается через `invoke_with_trace` в analyst_graph —
                # все 5 span'ов одного запроса попадают в один trace.
                from nefteboros.observability.tracer import Trace
                import time as _time
                import uuid as _uuid
                from datetime import datetime as _dt, timezone as _tz

                trace = Trace(
                    trace_id=f"orphan_{_uuid.uuid4().hex[:8]}",
                    started_at=_time.monotonic(),
                    ts_iso=_dt.now(_tz.utc).isoformat(timespec="milliseconds"),
                    query=None,
                )
                logger.debug(
                    "observe(%s): no current trace, writing orphan span", node_name
                )

            # Compact input для span: первый аргумент — обычно GraphState.
            input_data: Optional[dict[str, Any]] = None
            if args:
                first = args[0]
                if hasattr(first, "model_dump"):
                    try:
                        input_data = {"state_keys": list(first.model_dump().keys())}
                    except Exception:  # noqa: BLE001
                        input_data = None
                elif isinstance(first, dict):
                    input_data = {"state_keys": list(first.keys())}

            span = tracer.start_span(node_name, trace=trace, input_data=input_data)
            span_token = _current_span.set(span)

            try:
                result = await fn(*args, **kwargs)
            except BaseException as exc:
                tracer.end_span(span, status="error", error=exc, trace=trace)
                raise
            finally:
                _current_span.reset(span_token)

            output_data: Optional[dict[str, Any]] = None
            if isinstance(result, dict):
                output_data = {"keys": list(result.keys())}
            tracer.end_span(span, status="ok", output_data=output_data, trace=trace)

            return result

        return wrapped  # type: ignore[return-value]

    return decorator


def start_trace(*, query: Optional[str] = None) -> Trace:
    """Открыть top-level trace для одного запроса агента.

    Должен вызываться на entry-point (CLI / Telegram bot / Ouroboros tool
    handler) перед `graph.ainvoke(...)`. Span'ы внутри `observe`-декорированных
    узлов привязываются к этому trace через contextvars.
    """
    tracer = get_tracer()
    trace = tracer.start_trace(query=query)
    _current_trace.set(trace)
    return trace


def end_trace(trace: Trace, *, answer: Optional[str] = None) -> None:
    """Закрыть top-level trace и flush в Langfuse + JSONL summary.

    Должен вызываться даже при ошибке (через try/finally), чтобы trace
    закрылся и не остался висеть в Langfuse.
    """
    tracer = get_tracer()
    tracer.end_trace(trace, answer=answer)


__all__ = [
    "observe",
    "log_llm_usage",
    "start_trace",
    "end_trace",
    "Span",
    "Trace",
]
