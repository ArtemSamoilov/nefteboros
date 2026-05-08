"""Observability — Langfuse трейсинг + JSONL backup для LangGraph узлов.

См. ADR-0024 (`docs/adr/0024-observability-langfuse.md`).

**Архитектура (после рефакторинга 2026-05-08):**

- `traced_tool` / `observe` — тонкие обёртки над **встроенным** `langfuse.observe`
  декоратором. Langfuse SDK сам управляет OTel context'ом, иерархией родитель-
  потомок, ingest'ом content в UI. Мы добавляем поверх:
    1. session_id / user_id из `ctx.current_chat_id` через `update_current_trace`.
    2. JSON-trace в `metrics/runs/<ts>/trace.jsonl` (offline backup).
    3. log_llm_usage помощник для проброса tokens/cost в текущий generation span.
- `_Tracer` (tracer.py) — теперь **только JSONL writer + registry для группировки
  span'ов одного user-request в JSON-trace** (Langfuse-связь полностью удалена,
  её делает SDK).

Использование:

    from nefteboros.observability import observe, log_llm_usage, traced_tool

    @traced_tool(name="analyst_query")
    def _tool_analyst_query(ctx=None, *, query: str = "") -> str:
        ...

    builder.add_node("synthesize", observe(name="synthesize", as_type="generation")(synthesize))

    # Внутри LLM-узла после chat-call:
    msg, usage = await client.chat_async(...)
    log_llm_usage(usage)

Errors никогда не попадают в финальный ответ агента или UI пользователя —
только в Python logging. Если Langfuse SDK не установлен / Cloud недоступен —
JSON-trace продолжает писаться независимо.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import time
from typing import Any, Callable, Optional, TypeVar

from nefteboros.observability.tracer import (
    Span,
    Trace,
    _current_span,
    _current_trace,
    get_tracer,
    log_llm_usage,
)

logger = logging.getLogger(__name__)


F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Lazy import Langfuse SDK
# =============================================================================


_LF_OBSERVE: Optional[Callable[..., Any]] = None
_LF_GET_CLIENT: Optional[Callable[[], Any]] = None
_LF_AVAILABLE: Optional[bool] = None


def _try_import_langfuse() -> bool:
    """Попытка импорта langfuse SDK с feature-flag.

    Кешируем результат — без повторных try/import на каждый span.
    Если LANGFUSE_ENABLED=false — пропускаем даже импорт (быстрее CI).
    """
    global _LF_OBSERVE, _LF_GET_CLIENT, _LF_AVAILABLE
    if _LF_AVAILABLE is not None:
        return _LF_AVAILABLE

    flag = os.environ.get("LANGFUSE_ENABLED", "true").strip().lower()
    if flag in ("false", "0", "no", ""):
        _LF_AVAILABLE = False
        return False

    try:
        from langfuse import get_client, observe

        _LF_OBSERVE = observe
        _LF_GET_CLIENT = get_client
        _LF_AVAILABLE = True
        return True
    except ImportError:
        logger.info("langfuse SDK not installed — JSON-trace only")
        _LF_AVAILABLE = False
        return False


def _extract_request_id(ctx: Any) -> Optional[str]:
    """task_id (приоритет) или current_chat_id из ouroboros ToolContext."""
    if ctx is None:
        return None
    task_id = getattr(ctx, "task_id", None)
    if task_id:
        return f"task:{task_id}"
    chat_id = getattr(ctx, "current_chat_id", None)
    if chat_id:
        return f"chat:{chat_id}"
    return None


def _extract_session_id(ctx: Any) -> Optional[str]:
    """current_chat_id → session_id для Langfuse (группировка по чат-сессии)."""
    if ctx is None:
        return None
    chat_id = getattr(ctx, "current_chat_id", None)
    return f"chat:{chat_id}" if chat_id is not None else None


# =============================================================================
# Public API: decorators
# =============================================================================


def observe(
    *, name: Optional[str] = None, as_type: str = "span"
) -> Callable[[F], F]:
    """Декоратор для LangGraph узлов — тонкая обёртка над `langfuse.observe`.

    Применяется через wrap при `add_node` в `analyst_graph.py`, чтобы файлы
    `nefteboros/graphs/nodes/*.py` оставались чистыми (см. ADR-0025 §«Где
    лежат декораторы»).

    - Langfuse SDK сам управляет OTel context'ом — вложенные @observe-функции
      автоматически становятся child observations.
    - JSON-trace параллельно пишется через нашу обёртку (для offline debug
      без Langfuse).

    Args:
        name: имя span'а в Langfuse UI (default — fn.__name__).
        as_type: "span" / "generation" / "tool" / etc — тип observation в UI.
    """

    def decorator(fn: F) -> F:
        node_name = name or fn.__name__
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(
                f"observe(name={node_name!r}) применён к sync-функции; "
                "узлы LangGraph в analyst_graph должны быть async."
            )

        # Langfuse native @observe — иерархия / OTel context / content
        # auto-capture через SDK. JSON-trace параллельно через нашу обёртку.
        if _try_import_langfuse() and _LF_OBSERVE is not None:
            fn = _LF_OBSERVE(name=node_name, as_type=as_type)(fn)  # type: ignore[assignment]

        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            # JSON-trace span (async-friendly).
            tracer = get_tracer()
            trace = _current_trace.get()
            if trace is None:
                from nefteboros.observability.tracer import Trace as _T
                from datetime import datetime as _dt, timezone as _tz
                import uuid as _uuid

                trace = _T(
                    trace_id=f"orphan_{_uuid.uuid4().hex[:8]}",
                    started_at=time.monotonic(),
                    ts_iso=_dt.now(_tz.utc).isoformat(timespec="milliseconds"),
                    query=None,
                )

            json_input = args[0] if args else None
            input_compact = None
            if hasattr(json_input, "model_dump"):
                try:
                    input_compact = {"state_keys": list(json_input.model_dump().keys())}
                except Exception:  # noqa: BLE001
                    input_compact = None

            span = tracer.start_span(
                node_name,
                trace=trace,
                input_data=json_input,
                input_compact=input_compact,
                as_type=as_type,
            )
            span_token = _current_span.set(span)
            try:
                result = await fn(*args, **kwargs)
            except BaseException as exc:
                tracer.end_span(span, status="error", error=exc, trace=trace)
                raise
            finally:
                _current_span.reset(span_token)

            output_compact: Optional[dict[str, Any]] = None
            if isinstance(result, dict):
                output_compact = {"keys": list(result.keys())}
            tracer.end_span(
                span,
                status="ok",
                output_data=result,
                output_compact=output_compact,
                trace=trace,
            )
            return result

        return wrapped  # type: ignore[return-value]

    return decorator


def traced_tool(
    *, name: Optional[str] = None, query_arg: str = "query"
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Декоратор для Ouroboros tool entry points — wrapper над `langfuse.observe`.

    Поведение:
    1. **Langfuse**: @observe(as_type="tool") создаёт root span "tool_name" →
       устанавливает trace.name через `update_current_trace(name=...)` если
       это первый tool в task. session_id / user_id из ctx.current_chat_id.
       OTel context propagation делает SDK автоматически — следующие @observe
       вызовы (в т.ч. graph узлы внутри analyst_query) становятся child.
    2. **JSON-trace**: ctx-aware registry группирует span'ы одного user-request
       в один trace_id. Закрытие — TTL / atexit / явный `close_trace_for_request`.

    Сигнатура handler'а: `def fn(ctx: Any = None, *, query: str = "")`.
    Ouroboros вызывает `handler(ctx, **args)`; default `ctx=None` поддерживает
    legacy `handler(**args)` fallback при TypeError.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            ctx = args[0] if args else None
            query_value: Optional[str] = None
            v = kwargs.get(query_arg)
            if isinstance(v, str):
                query_value = v

            # JSON-trace registry (для offline backup).
            tracer = get_tracer()
            request_id = _extract_request_id(ctx)
            session_id = _extract_session_id(ctx)
            trace, is_new = tracer.get_or_create_trace_for_request(
                request_id, query_value, name="user_request", session_id=session_id
            )
            is_legacy = is_new and not request_id

            # Session attribution + JSON-trace sync делаются ниже внутри _inner
            # (см. SDK 4.x: update_current_trace отсутствует, используется
            # update_current_span с metadata).

            # Span конкретного tool в JSON-trace (зеркало Langfuse @observe span).
            trace_token = _current_trace.set(trace)
            tool_span = tracer.start_span(
                tool_name,
                trace=trace,
                input_data={"query": query_value} if query_value else None,
                as_type="tool",
            )
            span_token = _current_span.set(tool_span)

            try:
                # Native Langfuse @observe — иерархия / OTel context / content
                # auto-capture. Sync JSON-trace.trace_id с Langfuse OTel ID для
                # verify-скрипта. session_id ставится через update_current_span
                # (4.x API; update_current_trace не существует).
                def _inner(*a: Any, **kw: Any) -> Any:
                    if _try_import_langfuse() and _LF_GET_CLIENT is not None:
                        try:
                            client = _LF_GET_CLIENT()
                            # Sync trace_id JSON-trace ↔ Langfuse OTel.
                            lf_tid = client.get_current_trace_id()
                            if lf_tid and is_new:
                                trace.trace_id = str(lf_tid)
                            # session_id ставим в metadata КАЖДОГО tool вызова
                            # (а не только первого) — чтобы все 3 trace'а
                            # одной чат-сессии можно было найти filter'ом
                            # `metadata.session_id` в UI.
                            if session_id:
                                try:
                                    client.update_current_span(
                                        metadata={
                                            "session_id": session_id,
                                            "request_id": request_id,
                                        },
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                        except Exception:  # noqa: BLE001
                            pass
                    return fn(*a, **kw)

                if _try_import_langfuse() and _LF_OBSERVE is not None:
                    inner_wrapped = _LF_OBSERVE(name=tool_name, as_type="tool")(_inner)
                else:
                    inner_wrapped = _inner

                result = inner_wrapped(*args, **kwargs)

                # Парсим JSON-string результат tool'а для full output.
                output_full: Any = result
                output_compact: Optional[dict[str, Any]] = None
                if isinstance(result, str):
                    try:
                        output_full = json.loads(result)
                    except (json.JSONDecodeError, ValueError):
                        output_full = result
                    output_compact = {"answer_chars": len(result)}

                tracer.end_span(
                    tool_span,
                    status="ok",
                    output_data=output_full,
                    output_compact=output_compact,
                    trace=trace,
                )
                if is_legacy:
                    end_trace(
                        trace,
                        answer=result if isinstance(result, str) else None,
                        answer_full=output_full,
                    )
                return result
            except BaseException as exc:
                tracer.end_span(tool_span, status="error", error=exc, trace=trace)
                if is_legacy:
                    end_trace(trace, answer=None)
                raise
            finally:
                _current_span.reset(span_token)
                _current_trace.reset(trace_token)

        return wrapped

    return decorator


def start_trace(
    *,
    query: Optional[str] = None,
    name: str = "analyst_request",
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Trace:
    """Открыть top-level JSON-trace (для CLI / eval — без Ouroboros).

    Note: Langfuse trace будет создан автоматически при первом @observe
    декорированном вызове внутри. Этот API только для JSON-trace.
    """
    tracer = get_tracer()
    trace = tracer.start_trace(
        query=query, name=name, session_id=session_id, user_id=user_id
    )
    _current_trace.set(trace)
    return trace


def end_trace(
    trace: Trace,
    *,
    answer: Optional[str] = None,
    answer_full: Optional[Any] = None,
) -> None:
    """Закрыть JSON-trace (Langfuse trace закрывается SDK автоматически)."""
    tracer = get_tracer()
    tracer.end_trace(trace, answer=answer, answer_full=answer_full)


__all__ = [
    "observe",
    "traced_tool",
    "log_llm_usage",
    "start_trace",
    "end_trace",
    "Span",
    "Trace",
]
