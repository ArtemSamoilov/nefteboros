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


def observe(
    *, name: Optional[str] = None, as_type: str = "span"
) -> Callable[[F], F]:
    """Декоратор / wrapper для async-узлов LangGraph.

    Применяется через wrap при `add_node` в `analyst_graph.py`, чтобы файлы
    `nefteboros/graphs/nodes/*.py` оставались чистыми (см. ADR-0024 §«Где
    лежат декораторы»).

    Поведение:
    - Перед вызовом — start_span(node=name, as_type=as_type).
    - Контекст span'а доступен через `_current_span.get()` для `log_llm_usage`.
    - После вызова — end_span(status="ok"|"error", output, error).
    - Узел не падает — exception ре-райзится наружу (LangGraph сам обработает).
    - Если top-level trace не открыт (graph вызван без `start_trace`) —
      пишется orphan-span (только в JSON-trace, без Langfuse-привязки).

    Args:
        name: имя узла в трейсе. Если None — берётся `fn.__name__`.
        as_type: "span" (default) для не-LLM узлов; "generation" для LLM-узлов
                 (synthesize, llm_disambiguate) — тогда в Langfuse UI узел
                 рисуется как chat-message с tokens / cost / model.
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

            span = tracer.start_span(
                node_name, trace=trace, input_data=input_data, as_type=as_type
            )
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


def start_trace(
    *, query: Optional[str] = None, name: str = "analyst_request"
) -> Trace:
    """Открыть top-level trace для одного запроса агента.

    Должен вызываться на entry-point (CLI / Telegram bot / Ouroboros tool
    handler) перед `graph.ainvoke(...)`. Span'ы внутри `observe`-декорированных
    узлов привязываются к этому trace через contextvars.

    Args:
        query: пользовательский запрос (input root observation).
        name: имя top-level trace в Langfuse UI. По умолчанию analyst_request;
            для rag_search / web_search tool entry — передавайте соответственно.
    """
    tracer = get_tracer()
    trace = tracer.start_trace(query=query, name=name)
    _current_trace.set(trace)
    return trace


def end_trace(trace: Trace, *, answer: Optional[str] = None) -> None:
    """Закрыть top-level trace и flush в Langfuse + JSONL summary.

    Должен вызываться даже при ошибке (через try/finally), чтобы trace
    закрылся и не остался висеть в Langfuse.
    """
    tracer = get_tracer()
    tracer.end_trace(trace, answer=answer)


def _extract_request_id(ctx: Any) -> Optional[str]:
    """Достать request-id (task_id или chat_id) из ouroboros ToolContext.

    Один user-request → один trace. ToolContext общий для всех tool вызовов
    одного task'а в ouroboros loop.py — task_id используется как primary
    ключ. current_chat_id как fallback (один чат = одна сессия).
    """
    if ctx is None:
        return None
    task_id = getattr(ctx, "task_id", None)
    if task_id:
        return f"task:{task_id}"
    chat_id = getattr(ctx, "current_chat_id", None)
    if chat_id:
        return f"chat:{chat_id}"
    return None


def traced_tool(
    *, name: Optional[str] = None, query_arg: str = "query"
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Декоратор для Ouroboros tool entry points — group по user-request.

    Применяется к sync tool handler'ам в `skills/*/plugin.py`. Ouroboros
    вызывает их как `handler(ctx, **args)`, где `ctx.task_id` общий для всех
    tool вызовов одного user-request (см. `ouroboros.tools.registry.ToolContext`).

    **Один user-request = один trace в Langfuse:**
    - Первый tool вызов открывает trace, кладёт в registry по `ctx.task_id`.
    - Последующие tools (любого имени) добавляют свои span'ы в **тот же**
      trace через `trace_context={"trace_id": ...}`.
    - Trace закрывается по TTL (10 мин без новых вызовов) или atexit.

    **Узлы LangGraph внутри analyst_query** прицепляются к user-request
    trace через contextvars (asyncio.run пропагирует context в child loop).

    Сигнатура handler'а: `def my_tool(ctx=None, *, query: str = "")` —
    ouroboros сначала пробует `handler(ctx, **args)`, при TypeError —
    `handler(**args)`. Default `ctx=None` поддерживает оба вызова.

    Args:
        name: имя для span'а текущего tool в Langfuse UI (default — fn.__name__).
            На root trace имя берётся из первого tool в request'е.
        query_arg: имя kwarg с user-query (для input root observation).
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            # Извлекаем ctx (первый позиционный) и query.
            ctx = args[0] if args else None
            query_value: Optional[str] = None
            v = kwargs.get(query_arg)
            if isinstance(v, str):
                query_value = v

            tracer = get_tracer()
            request_id = _extract_request_id(ctx)

            # Получить или открыть trace на user-request. Если ctx.task_id
            # отсутствует (legacy/CLI вызов), trace не регистрируется — мы
            # его закрываем сразу после tool вызова, иначе он висит до atexit.
            trace, is_new = tracer.get_or_create_trace_for_request(
                request_id, query_value, name="user_request"
            )
            is_legacy_unregistered = is_new and not request_id

            trace_token = _current_trace.set(trace)
            tool_span = tracer.start_span(
                tool_name,
                trace=trace,
                input_data={"query": query_value} if query_value else None,
                as_type="tool",
            )
            span_token = _current_span.set(tool_span)

            try:
                result = fn(*args, **kwargs)
                output_data: Optional[dict[str, Any]] = None
                if isinstance(result, str):
                    output_data = {"answer_chars": len(result)}
                tracer.end_span(
                    tool_span, status="ok", output_data=output_data, trace=trace
                )
                if is_legacy_unregistered:
                    # Legacy путь без ctx — закрываем trace сразу, иначе он
                    # висит до atexit без summary-строки.
                    end_trace(trace, answer=result if isinstance(result, str) else None)
                return result
            except BaseException as exc:
                tracer.end_span(tool_span, status="error", error=exc, trace=trace)
                if is_legacy_unregistered:
                    end_trace(trace, answer=None)
                raise
            finally:
                _current_span.reset(span_token)
                _current_trace.reset(trace_token)

        return wrapped

    return decorator


__all__ = [
    "observe",
    "traced_tool",
    "log_llm_usage",
    "start_trace",
    "end_trace",
    "Span",
    "Trace",
]
