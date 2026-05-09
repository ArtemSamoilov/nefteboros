"""Observability — Langfuse трейсинг для агента.

См. ADR-0024 (`docs/adr/0024-observability-langfuse.md`).

**Архитектура:**

- `traced_tool` — декоратор для Ouroboros tool entry points в
  `skills/*/plugin.py`. Открывает `langfuse.propagate_attributes` контекст
  с `session_id` / `user_id` из `ctx.current_chat_id` и фиксирует
  `trace_name="user_request"` — все 3 tool вызова одной чат-сессии в UI
  попадают в одну Langfuse Session с одинаковым именем trace'а.
- `observe` — декоратор для async-узлов LangGraph. Тонкая обёртка над
  встроенным `langfuse.observe` — иерархия parent-child делается SDK
  через OTel context propagation.
- `log_llm_usage` — пробрасывает tokens / cost / model из usage-словаря
  ouroboros или langchain в текущий generation-observation Langfuse через
  `client.update_current_generation`.

JSON-trace (`metrics/runs/<ts>/trace.jsonl`) пишется параллельно через
`_Tracer` — это offline backup для дебага без Langfuse Cloud (см.
`tracer.py`). Group span'ов делается **только** в Langfuse через session_id —
JSON-trace на это не претендует.

Errors не попадают в финальный ответ агента или UI пользователя — только
в Python `logging`. Если Langfuse SDK не установлен — JSON-trace продолжает.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
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
# Lazy import langfuse SDK
# =============================================================================

_LF_OBSERVE: Optional[Callable[..., Any]] = None
_LF_PROPAGATE: Optional[Callable[..., Any]] = None
_LF_AVAILABLE: Optional[bool] = None


def _try_import_langfuse() -> bool:
    """Lazy import langfuse SDK с feature-flag.

    Кешируем результат — без повторных try/import на каждый span.
    `LANGFUSE_ENABLED=false` пропускает даже импорт (ускоряет CI / unit-тесты).
    """
    global _LF_OBSERVE, _LF_PROPAGATE, _LF_AVAILABLE
    if _LF_AVAILABLE is not None:
        return _LF_AVAILABLE

    flag = os.environ.get("LANGFUSE_ENABLED", "true").strip().lower()
    if flag in ("false", "0", "no", ""):
        _LF_AVAILABLE = False
        return False

    try:
        from langfuse import observe, propagate_attributes

        _LF_OBSERVE = observe
        _LF_PROPAGATE = propagate_attributes
        _LF_AVAILABLE = True
        return True
    except ImportError:
        logger.info("langfuse SDK not installed — JSON-trace only")
        _LF_AVAILABLE = False
        return False


def _extract_session_id(ctx: Any) -> Optional[str]:
    """`ctx.current_chat_id` → session_id для Langfuse.

    Несколько user-requests одного чата (разные `task_id`) попадают в одну
    Langfuse Session — UI Sessions tab группирует их.
    """
    if ctx is None:
        return None
    chat_id = getattr(ctx, "current_chat_id", None)
    return f"chat:{chat_id}" if chat_id is not None else None


def _extract_user_id(ctx: Any) -> Optional[str]:
    """`ctx.user_id` если есть. Сейчас в ouroboros ToolContext не предусмотрено,
    но оставляем точку расширения. None — пропускаем."""
    if ctx is None:
        return None
    return getattr(ctx, "user_id", None)


# =============================================================================
# Public API: decorators
# =============================================================================


def observe(
    *, name: Optional[str] = None, as_type: str = "span"
) -> Callable[[F], F]:
    """Декоратор для async-узлов LangGraph — обёртка над `langfuse.observe`.

    Применяется через wrap при `add_node` в `analyst_graph.py`, чтобы файлы
    `nefteboros/graphs/nodes/*.py` оставались доменными (см. ADR-0025 §«Где
    лежат декораторы»).

    Поведение:
    - Langfuse SDK сам управляет OTel context'ом: вложенные @observe
      становятся child observations при пропагации через async/await.
    - JSON-trace пишется через `_Tracer.start_span` / `end_span` параллельно
      (offline backup).

    Args:
        name: имя observation в Langfuse (default fn.__name__).
        as_type: "span" / "generation" / "tool" / etc — типизация в UI.
    """

    def decorator(fn: F) -> F:
        node_name = name or fn.__name__
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(
                f"observe(name={node_name!r}) применён к sync-функции; "
                "узлы LangGraph должны быть async."
            )

        if _try_import_langfuse() and _LF_OBSERVE is not None:
            fn = _LF_OBSERVE(name=node_name, as_type=as_type)(fn)  # type: ignore[assignment]

        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            trace = _current_trace.get()
            if trace is None:
                # Orphan-trace для unit-тестов / прямых вызовов узлов.
                trace = _make_orphan_trace()

            input_compact = None
            input_full: Any = args[0] if args else None
            if hasattr(input_full, "model_dump"):
                try:
                    input_compact = {"state_keys": list(input_full.model_dump().keys())}
                except Exception:  # noqa: BLE001
                    pass

            span = tracer.start_span(
                node_name,
                trace=trace,
                input_data=input_full,
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

            output_compact = (
                {"keys": list(result.keys())} if isinstance(result, dict) else None
            )
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
    """Декоратор для Ouroboros tool entry points.

    Применяется к sync handler'ам в `skills/*/plugin.py`.

    Что делает:

    1. **`langfuse.propagate_attributes`**: открывает контекст с
       `session_id=chat:N`, `user_id`, `trace_name="user_request"`. Все
       observations созданные внутри (в т.ч. вложенные @observe графа)
       наследуют эти trace-level атрибуты. UI Sessions tab → видим
       все tool вызовы одного чата как одну сессию; UI Traces tab →
       все trace'ы с именем `user_request`.
    2. **`langfuse.observe(as_type="tool")`** оборачивает реальный handler
       — создаёт root observation с auto-capture input/output, иерархия
       parent-child делается SDK.
    3. **JSON-trace** через `_Tracer` — параллельный offline backup на
       диске (для дебага без Langfuse Cloud).

    Сигнатура handler'а: `def fn(ctx: Any = None, *, query: str = "")`.
    Ouroboros вызывает `handler(ctx, **args)`; default `ctx=None`
    поддерживает legacy `handler(**args)` fallback при TypeError.

    Args:
        name: имя tool span'а в Langfuse (default fn.__name__).
        query_arg: имя kwarg с пользовательским запросом — для JSON-trace.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            ctx = args[0] if args else None
            query_value: Optional[str] = (
                kwargs.get(query_arg) if isinstance(kwargs.get(query_arg), str) else None
            )
            session_id = _extract_session_id(ctx)
            user_id = _extract_user_id(ctx)

            # ---- JSON-trace span (offline backup) ----
            tracer = get_tracer()
            trace = tracer.start_trace(
                query=query_value,
                name="user_request",
                session_id=session_id,
                user_id=user_id,
            )
            trace_token = _current_trace.set(trace)
            tool_span = tracer.start_span(
                tool_name,
                trace=trace,
                input_data={"query": query_value} if query_value else None,
                as_type="tool",
            )
            span_token = _current_span.set(tool_span)

            # ---- Langfuse: propagate_attributes + @observe ----
            if _try_import_langfuse() and _LF_OBSERVE is not None and _LF_PROPAGATE is not None:
                lf_observed = _LF_OBSERVE(name=tool_name, as_type="tool")(fn)
                propagate_kwargs: dict[str, Any] = {"trace_name": "user_request"}
                if session_id:
                    propagate_kwargs["session_id"] = session_id
                if user_id:
                    propagate_kwargs["user_id"] = user_id
                cm = _LF_PROPAGATE(**propagate_kwargs)
            else:
                lf_observed = fn
                cm = _NullContext()

            try:
                with cm:
                    result = lf_observed(*args, **kwargs)

                # Парсим JSON-string tool result для full output.
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
                tracer.end_trace(
                    trace,
                    answer=result if isinstance(result, str) else None,
                    answer_full=output_full,
                )
                return result
            except BaseException as exc:
                tracer.end_span(tool_span, status="error", error=exc, trace=trace)
                tracer.end_trace(trace, answer=None)
                raise
            finally:
                _current_span.reset(span_token)
                _current_trace.reset(trace_token)

        return wrapped

    return decorator


# =============================================================================
# Helpers
# =============================================================================


class _NullContext:
    """No-op context manager — для пути без Langfuse."""

    def __enter__(self) -> "_NullContext":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _make_orphan_trace() -> Trace:
    """Trace для async-узла, вызванного без обёртки `traced_tool` (unit-тесты,
    direct invocation). JSON-trace пишет orphan span; Langfuse не
    затрагивается (в этом контексте OTel-spanа всё равно нет)."""
    import time as _time
    import uuid as _uuid
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    return Trace(
        trace_id=f"orphan_{_uuid.uuid4().hex[:8]}",
        started_at=_time.monotonic(),
        ts_iso=_dt.now(_tz.utc).isoformat(timespec="milliseconds"),
        query=None,
    )


# =============================================================================
# Top-level trace API (для CLI / eval скриптов)
# =============================================================================


def start_trace(
    *,
    query: Optional[str] = None,
    name: str = "analyst_request",
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Trace:
    """Открыть top-level JSON-trace (CLI / eval — без Ouroboros tool dispatch).

    Note: Langfuse trace создаётся SDK автоматически при первом @observe-
    декорированном вызове. Эта функция только для JSON-trace.
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
    """Закрыть JSON-trace. Langfuse trace закрывается SDK автоматически."""
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
