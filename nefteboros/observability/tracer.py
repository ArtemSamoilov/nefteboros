"""Tracer — JSONL writer для observability.

См. ADR-0024-observability-langfuse.

**Scope:** offline backup в `metrics/runs/<ts>/trace.jsonl` для дебага
без Langfuse Cloud. Langfuse-сторона делается через native `@observe` +
`propagate_attributes` в `nefteboros/observability/__init__.py`.

Concurrency-safe append: каждая JSON-line ≤ ~5KB → POSIX `O_APPEND` atomic
для блоков ≤ PIPE_BUF (4096 bytes на Linux/macOS). См. ассерт TRUNCATE_THRESHOLD.

Контекст-vars: `_current_span` / `_current_trace` пробрасывают активный
JSON-span в `log_llm_usage` для прикрепления tokens / cost.
"""

from __future__ import annotations

import contextvars
import dataclasses
import json
import logging
import os
import pathlib
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from nefteboros.observability.cost import compute_cost

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Truncate threshold для JSON-trace input/output. Атомарность POSIX
# `O_APPEND` гарантируется для блоков ≤ PIPE_BUF=4096 на Linux/macOS.
# JSON-line содержит max ~2 поля (input + output) + ~500 bytes метаданных.
TRUNCATE_THRESHOLD = 2048
_PIPE_BUF_LINUX_MACOS = 4096
assert TRUNCATE_THRESHOLD * 2 + 500 < _PIPE_BUF_LINUX_MACOS * 2, (
    "JSON-line size at risk of exceeding atomic-append guarantee. "
    "Either lower TRUNCATE_THRESHOLD or implement a writer-side mutex."
)


# =============================================================================
# Span / Trace dataclasses
# =============================================================================


@dataclasses.dataclass
class Span:
    """Один узел в JSON-trace. Mutable — поля заполняются по ходу выполнения.

    Поля `*_full` хранят полный объект для отдельного канала (если будет
    нужно читать в будущем). Сейчас в JSONL пишется только compact-версия
    (поля `input` / `output`).
    """

    trace_id: str
    span_id: int
    node: str
    started_at: float
    ts_iso: str
    parent_span_id: Optional[int] = None
    status: str = "ok"
    input: Optional[Any] = None
    output: Optional[Any] = None
    input_full: Optional[Any] = None
    output_full: Optional[Any] = None
    error: Optional[dict[str, str]] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost_usd: Optional[float] = None


@dataclasses.dataclass
class Trace:
    """Top-level запись запроса с агрегатами для JSON summary."""

    trace_id: str
    started_at: float
    ts_iso: str
    query: Optional[str] = None
    answer: Optional[str] = None
    answer_full: Optional[Any] = None
    span_count: int = 0
    status: str = "ok"
    error_node: Optional[str] = None
    total_cost_usd: float = 0.0
    session_id: Optional[str] = None
    user_id: Optional[str] = None


# =============================================================================
# ContextVars
# =============================================================================

_current_span: contextvars.ContextVar[Optional[Span]] = contextvars.ContextVar(
    "nefteboros_current_span", default=None
)
_current_trace: contextvars.ContextVar[Optional[Trace]] = contextvars.ContextVar(
    "nefteboros_current_trace", default=None
)


# =============================================================================
# Tracer singleton
# =============================================================================


class _Tracer:
    """JSONL writer singleton. Lazy-открытие файла при первой записи.

    Path: `OBSERVABILITY_RUN_DIR/trace.jsonl` (env override) или
    `<repo>/metrics/runs/<utcnow>/trace.jsonl` (default).
    """

    def __init__(self) -> None:
        self._jsonl_path: Optional[pathlib.Path] = None
        self._jsonl_handle: Any = None
        self._lock = threading.Lock()
        self._next_span_id = 1

    # --- run dir / jsonl path ---

    def _resolve_run_dir(self) -> pathlib.Path:
        env_dir = os.environ.get("OBSERVABILITY_RUN_DIR", "").strip()
        if env_dir:
            return pathlib.Path(env_dir)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cwd = pathlib.Path.cwd()
        for parent in [cwd] + list(cwd.parents):
            if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
                return parent / "metrics" / "runs" / ts
        return cwd / "metrics" / "runs" / ts

    def _ensure_jsonl(self) -> Optional[Any]:
        if self._jsonl_handle is not None:
            return self._jsonl_handle
        run_dir = self._resolve_run_dir()
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = run_dir / "trace.jsonl"
            self._jsonl_handle = self._jsonl_path.open(
                "a", buffering=1, encoding="utf-8"
            )
            logger.info("JSON-trace writer: %s", self._jsonl_path)
        except OSError as exc:
            logger.warning("Cannot open JSON-trace file: %s", exc)
            return None
        return self._jsonl_handle

    # --- span_id ---

    def next_span_id(self) -> int:
        with self._lock:
            sid = self._next_span_id
            self._next_span_id += 1
            return sid

    # --- serialize / truncate ---

    @staticmethod
    def _serialize(value: Any) -> Any:
        """Произвольный объект → JSON-совместимый. Используется для full-копии
        (если понадобится читать обратно). Pydantic v2 / v1 / dataclass / Enum
        поддерживаются явно."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump(mode="json")
            except Exception:  # noqa: BLE001
                pass
        if hasattr(value, "_asdict"):
            try:
                return _Tracer._serialize(value._asdict())
            except Exception:  # noqa: BLE001
                pass
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            try:
                return {
                    f.name: _Tracer._serialize(getattr(value, f.name))
                    for f in dataclasses.fields(value)
                }
            except Exception:  # noqa: BLE001
                pass
        if (
            hasattr(value, "value")
            and hasattr(value, "name")
            and any("Enum" in b.__name__ for b in type(value).__mro__)
        ):
            return value.value
        if isinstance(value, dict):
            return {str(k): _Tracer._serialize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_Tracer._serialize(v) for v in value]
        return str(value)

    @staticmethod
    def _truncate(value: Any) -> Any:
        """Truncate string-fields до TRUNCATE_THRESHOLD bytes для JSONL.

        Используется ТОЛЬКО для JSON-trace на диске. В Langfuse content
        отправляется полностью через @observe SDK auto-capture.
        """
        if isinstance(value, str):
            if len(value.encode("utf-8")) > TRUNCATE_THRESHOLD:
                return {
                    "_preview": value[:TRUNCATE_THRESHOLD],
                    "_truncated": True,
                    "_len": len(value),
                }
            return value
        if isinstance(value, dict):
            return {k: _Tracer._truncate(v) for k, v in value.items()}
        if isinstance(value, list):
            if len(value) > 5:
                return {
                    "_count": len(value),
                    "_preview": [_Tracer._truncate(v) for v in value[:3]],
                }
            return [_Tracer._truncate(v) for v in value]
        return value

    def _write_jsonl(self, obj: dict[str, Any]) -> None:
        h = self._ensure_jsonl()
        if h is None:
            return
        try:
            h.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning("JSON-trace write failed: %s", exc)

    # --- public API: trace lifecycle ---

    def start_trace(
        self,
        query: Optional[str] = None,
        *,
        name: str = "user_request",
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Trace:
        """Открыть Trace для JSON-summary. Не пишет в JSONL — запись при end_trace.

        `name` хранится в Trace dataclass для возможного будущего использования,
        но в JSONL summary-line попадает только query/answer/agg.
        """
        return Trace(
            trace_id=str(uuid.uuid4()),
            started_at=time.monotonic(),
            ts_iso=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            query=query,
            session_id=session_id,
            user_id=user_id,
        )

    def end_trace(
        self,
        trace: Trace,
        answer: Optional[str] = None,
        *,
        answer_full: Optional[Any] = None,
    ) -> None:
        """Записать summary-строку (kind=trace) в JSONL."""
        trace.answer = answer
        if answer_full is not None:
            trace.answer_full = self._serialize(answer_full)
        elif answer is not None:
            trace.answer_full = answer
        total_ms = int((time.monotonic() - trace.started_at) * 1000)

        record: dict[str, Any] = {
            "kind": "trace",
            "ts": trace.ts_iso,
            "trace_id": trace.trace_id,
            "query": trace.query,
            "total_latency_ms": total_ms,
            "total_cost_usd": (
                round(trace.total_cost_usd, 8) if trace.total_cost_usd else 0.0
            ),
            "span_count": trace.span_count,
            "status": trace.status,
        }
        if trace.session_id:
            record["session_id"] = trace.session_id
        if trace.user_id:
            record["user_id"] = trace.user_id
        if trace.status == "error" and trace.error_node:
            record["error_node"] = trace.error_node
        self._write_jsonl(record)

    # --- public API: span lifecycle ---

    def start_span(
        self,
        node: str,
        trace: Trace,
        input_data: Optional[Any] = None,
        as_type: str = "span",
        input_compact: Optional[Any] = None,
    ) -> Span:
        """Открыть Span. Не пишет в JSONL — запись при end_span."""
        full = self._serialize(input_data) if input_data is not None else None
        compact = (
            self._truncate(input_compact)
            if input_compact is not None
            else (self._truncate(full) if full is not None else None)
        )
        return Span(
            trace_id=trace.trace_id,
            span_id=self.next_span_id(),
            node=node,
            started_at=time.monotonic(),
            ts_iso=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            input=compact,
            input_full=full,
        )

    def end_span(
        self,
        span: Span,
        *,
        status: str = "ok",
        output_data: Optional[Any] = None,
        output_compact: Optional[Any] = None,
        error: Optional[BaseException] = None,
        trace: Optional[Trace] = None,
    ) -> None:
        """Закрыть Span: latency, статус, запись в JSONL."""
        latency_ms = int((time.monotonic() - span.started_at) * 1000)
        span.status = status
        if output_data is not None:
            full = self._serialize(output_data)
            span.output_full = full
            span.output = (
                self._truncate(output_compact)
                if output_compact is not None
                else self._truncate(full)
            )
        if error is not None:
            msg = str(error)
            if len(msg) > 500:
                msg = msg[:500] + "...[truncated]"
            span.error = {"type": type(error).__name__, "message": msg}

        if trace is not None:
            trace.span_count += 1
            if span.cost_usd:
                trace.total_cost_usd += span.cost_usd
            if status == "error" and trace.status == "ok":
                trace.status = "error"
                trace.error_node = span.node

        record: dict[str, Any] = {
            "kind": "span",
            "ts": span.ts_iso,
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "node": span.node,
            "status": span.status,
        }
        if status != "skipped":
            record["latency_ms"] = latency_ms
        if span.input is not None:
            record["input"] = span.input
        if span.output is not None:
            record["output"] = span.output
        if span.error is not None:
            record["error"] = span.error
        if span.model is not None:
            record["model"] = span.model
        if span.provider is not None:
            record["provider"] = span.provider
        if span.prompt_tokens is not None:
            record["prompt_tokens"] = span.prompt_tokens
        if span.completion_tokens is not None:
            record["completion_tokens"] = span.completion_tokens
        if span.cost_usd is not None:
            record["cost_usd"] = round(span.cost_usd, 8)

        self._write_jsonl(record)


# =============================================================================
# Singleton accessor
# =============================================================================

_GLOBAL_TRACER: Optional[_Tracer] = None
_GLOBAL_LOCK = threading.Lock()


def get_tracer() -> _Tracer:
    global _GLOBAL_TRACER
    if _GLOBAL_TRACER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_TRACER is None:
                _GLOBAL_TRACER = _Tracer()
    return _GLOBAL_TRACER


def _reset_for_tests() -> None:
    """Reset singleton — для unit-тестов."""
    global _GLOBAL_TRACER
    with _GLOBAL_LOCK:
        if _GLOBAL_TRACER is not None and _GLOBAL_TRACER._jsonl_handle is not None:
            try:
                _GLOBAL_TRACER._jsonl_handle.close()
            except OSError:
                pass
        _GLOBAL_TRACER = None


# =============================================================================
# log_llm_usage — для LLM-узлов после chat-call
# =============================================================================


def log_llm_usage(
    usage: Optional[dict[str, Any]],
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> None:
    """Прикрепить tokens / cost к текущему span'у (JSON-trace + Langfuse).

    1. JSON-trace: заполнить поля `Span` для записи в trace.jsonl.
    2. Langfuse: проброс `usage_details` / `cost_details` через
       `client.update_current_generation` (4.x API).

    Args:
        usage: ouroboros usage dict (`prompt_tokens` / `completion_tokens`
            / `cost` / `resolved_model` / `provider`) или langchain
            `usage_metadata` (`input_tokens` / `output_tokens`).
            None → no-op.
        model: имя модели (override).
        provider: провайдер (override).
    """
    if usage is None:
        return

    span = _current_span.get()
    prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
    completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
    cached_tokens = usage.get("cached_tokens") or 0

    resolved_model = model or usage.get("resolved_model") or usage.get("model")
    if resolved_model:
        from nefteboros.observability.cost import _strip_provider_prefix

        resolved_model = _strip_provider_prefix(str(resolved_model))
    resolved_provider = provider or usage.get("provider")

    cost = usage.get("cost")
    if (
        cost is None
        and resolved_model
        and prompt_tokens is not None
        and completion_tokens is not None
    ):
        cost = compute_cost(
            resolved_model,
            int(prompt_tokens),
            int(completion_tokens),
            int(cached_tokens),
        )

    # 1. JSON-trace
    if span is not None:
        if prompt_tokens is not None:
            span.prompt_tokens = int(prompt_tokens)
        if completion_tokens is not None:
            span.completion_tokens = int(completion_tokens)
        if resolved_model:
            span.model = resolved_model
        if resolved_provider:
            span.provider = str(resolved_provider)
        if cost is not None:
            span.cost_usd = float(cost)

    # 2. Langfuse — текущий generation span
    try:
        from langfuse import get_client

        client = get_client()
        update_kwargs: dict[str, Any] = {}
        if resolved_model:
            update_kwargs["model"] = resolved_model
        usage_details: dict[str, int] = {}
        if prompt_tokens is not None:
            usage_details["input"] = int(prompt_tokens)
        if completion_tokens is not None:
            usage_details["output"] = int(completion_tokens)
        if usage_details:
            update_kwargs["usage_details"] = usage_details
        if cost is not None:
            update_kwargs["cost_details"] = {"total": float(cost)}
        if update_kwargs:
            client.update_current_generation(**update_kwargs)
    except ImportError:
        pass  # Langfuse not installed — JSON-trace already записан
    except Exception as exc:  # noqa: BLE001
        logger.debug("update_current_generation failed: %s", exc)


__all__ = [
    "Span",
    "Trace",
    "get_tracer",
    "log_llm_usage",
    "_current_span",
    "_current_trace",
    "_reset_for_tests",
]
