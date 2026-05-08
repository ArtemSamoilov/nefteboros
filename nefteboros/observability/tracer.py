"""Tracer — параллельная запись span'ов в JSONL + Langfuse Cloud.

Архитектура (см. ADR-0024):

    @observe-декоратор узла
       └→ span_start / span_end
              ├→ JSONL writer (всегда, write-through)
              └→ Langfuse SDK (опционально, через feature-flag)

JSONL — single file `<run_dir>/trace.jsonl`, append-mode. Concurrency-safe via
POSIX `O_APPEND` atomic-write для строк <PIPE_BUF=4KB. Каждый input/output
truncated до 2KB → строка ≤ ~5KB суммарно с метаданными — see TRUNCATE_THRESHOLD
assertion ниже.

Langfuse SDK импортируется **lazy**, только когда LANGFUSE_ENABLED=true и
есть ключи. При любой ошибке init (плохой ключ, network, RKN) — graceful
warning в logger, флаг внутри процесса форсится в false. Граф не падает.

Errors никогда не попадают в финальный ответ агента или UI пользователя —
только в Python logging (по требованию Артёма, см. dialogue 2026-05-08).
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

# Truncate threshold для inputs/outputs в JSON-trace. Обоснование:
# - POSIX `O_APPEND` write atomic для блоков ≤ PIPE_BUF (4096 bytes на
#   Linux/macOS).
# - Каждая JSON-line содержит max ~2 поля (input + output) + метаданные ~500 bytes.
# - 2 * 2048 + 500 ≈ 4596 — чуть > 4KB, но мы храним preview/summary, не raw —
#   реальный размер обычно ≪ threshold * 2. Assertion ниже — future-proofing
#   на случай если кто-то решит расширить threshold.
TRUNCATE_THRESHOLD = 2048
_PIPE_BUF_LINUX_MACOS = 4096
assert TRUNCATE_THRESHOLD * 2 + 500 < _PIPE_BUF_LINUX_MACOS * 2, (
    "JSON-line size at risk of exceeding atomic-append guarantee. "
    "Either lower TRUNCATE_THRESHOLD or implement a writer-side mutex. "
    "See nefteboros/observability/tracer.py docstring."
)


# =============================================================================
# Span / Trace dataclasses
# =============================================================================


@dataclasses.dataclass
class Span:
    """Один узел графа в трейсе. Mutable — поля заполняются по ходу выполнения."""

    trace_id: str
    span_id: int
    node: str
    started_at: float  # monotonic time для latency
    ts_iso: str  # человекочитаемый
    parent_span_id: Optional[int] = None
    status: str = "ok"  # ok | error | skipped
    input: Optional[dict[str, Any]] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[dict[str, str]] = None  # {"type", "message"}
    # LLM-only поля, опциональные
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost_usd: Optional[float] = None  # None=unknown, 0.0=точно бесплатно


@dataclasses.dataclass
class Trace:
    """Top-level запись с агрегатами по запросу."""

    trace_id: str
    started_at: float
    ts_iso: str
    query: Optional[str] = None
    answer: Optional[str] = None
    span_count: int = 0
    status: str = "ok"
    error_node: Optional[str] = None
    total_cost_usd: float = 0.0


# =============================================================================
# ContextVars — current span / current trace
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
    """Tracer singleton — JSONL writer + опциональный Langfuse client.

    Создаётся lazy на первом вызове `get_tracer()`. Файл открывается
    только при первой записи, чтобы пустые прогоны не оставляли мусора.
    """

    def __init__(self) -> None:
        self._jsonl_path: Optional[pathlib.Path] = None
        self._jsonl_handle: Any = None
        self._lock = threading.Lock()  # для span_id монотонности
        self._next_span_id = 1
        self._langfuse_enabled = False
        self._langfuse_client: Any = None
        self._langfuse_traces: dict[str, Any] = {}  # trace_id → langfuse root observation

        # Registry «один user-request = один trace»: ouroboros ToolContext
        # передаёт task_id (или current_chat_id) — common для всех tool
        # вызовов одного запроса. Первый tool открывает trace, последующие
        # переиспользуют. Очистка через TTL + atexit. См. ADR-0025
        # §«Trace lifecycle (e2e)».
        self._request_traces: dict[str, Trace] = {}
        self._request_traces_lock = threading.Lock()
        # Heartbeat: последняя активность по request_id, для TTL cleanup.
        self._request_last_seen: dict[str, float] = {}
        # Default TTL — 10 минут без новых tool вызовов → закрываем trace.
        self._request_ttl_sec = 600.0

        self._init_langfuse()
        self._register_atexit()

    # --- init ---

    def _init_langfuse(self) -> None:
        """Lazy import langfuse SDK. Любая ошибка — graceful, флаг false.

        Совместимо с langfuse SDK 4.x (current stable). API 4.x базируется на
        OpenTelemetry: client.start_observation(...) / span.update(...) /
        span.end() / client.flush(). Auth check на init — `auth_check()` —
        чтобы при плохих ключах не пытаться отправлять данные на каждый span.
        """
        flag = os.environ.get("LANGFUSE_ENABLED", "true").strip().lower()
        if flag in ("false", "0", "no", ""):
            return

        public = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        secret = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
        if not public or not secret:
            logger.info(
                "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY не заданы — "
                "Langfuse disabled, JSON-trace only."
            )
            return

        # Стандарт SDK 4.x — `host`. Alias `LANGFUSE_BASE_URL` поддерживается
        # как второе имя на случай если кто-то по аналогии с HYDRA_BASE_URL
        # выставил BASE_URL.
        host = (
            os.environ.get("LANGFUSE_HOST", "").strip()
            or os.environ.get("LANGFUSE_BASE_URL", "").strip()
            or "https://cloud.langfuse.com"
        )

        try:
            from langfuse import Langfuse

            self._langfuse_client = Langfuse(
                public_key=public,
                secret_key=secret,
                host=host,
            )
            # Auth check — проверяем что ключи валидные, прежде чем разрешить.
            try:
                if not self._langfuse_client.auth_check():
                    logger.warning(
                        "Langfuse auth_check failed — invalid keys? "
                        "JSON-trace продолжит работать."
                    )
                    self._langfuse_client = None
                    return
            except Exception as auth_exc:  # noqa: BLE001
                # auth_check сам может бросить (network/RKN-блок).
                logger.warning(
                    "Langfuse auth_check error: %s. JSON-trace продолжит работать.",
                    auth_exc,
                )
                self._langfuse_client = None
                return
            self._langfuse_enabled = True
            logger.info("Langfuse initialized: host=%s", host)
        except ImportError:
            logger.warning(
                "langfuse SDK не установлен (pip install langfuse). "
                "JSON-trace продолжит работать."
            )
        except Exception as exc:  # noqa: BLE001 — observability must not crash agent
            logger.warning(
                "Langfuse init failed: %s. JSON-trace продолжит работать.", exc
            )

    # --- run dir / jsonl path ---

    def _resolve_run_dir(self) -> pathlib.Path:
        """OBSERVABILITY_RUN_DIR (если выставлен Track D) или metrics/runs/<utcnow>/."""
        env_dir = os.environ.get("OBSERVABILITY_RUN_DIR", "").strip()
        if env_dir:
            return pathlib.Path(env_dir)

        # Дефолт: metrics/runs/<timestamp>/
        # Корень репо определяем по наличию .git / pyproject.toml выше cwd.
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cwd = pathlib.Path.cwd()
        for parent in [cwd] + list(cwd.parents):
            if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
                return parent / "metrics" / "runs" / ts
        return cwd / "metrics" / "runs" / ts

    def _ensure_jsonl(self) -> Optional[Any]:
        """Открыть jsonl-файл при первой записи. Возврат None — нет fs доступа."""
        if self._jsonl_handle is not None:
            return self._jsonl_handle

        run_dir = self._resolve_run_dir()
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = run_dir / "trace.jsonl"
            # `O_APPEND` атомарен для маленьких писем (<PIPE_BUF) на POSIX.
            # Открываем в text mode line-buffered.
            self._jsonl_handle = self._jsonl_path.open("a", buffering=1, encoding="utf-8")
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

    # --- write ---

    @staticmethod
    def _truncate(value: Any) -> Any:
        """Truncate string-fields в input/output до TRUNCATE_THRESHOLD bytes."""
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
                return {"_count": len(value), "_preview": [_Tracer._truncate(v) for v in value[:3]]}
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

    # --- atexit / TTL cleanup ---

    def _register_atexit(self) -> None:
        """Закрыть все активные traces и flush Langfuse при завершении процесса.

        Гарантирует что user-request trace, открытый первым tool вызовом,
        точно закроется — даже если последний tool не помечал свою итерацию
        как «завершающую».
        """
        import atexit

        atexit.register(self._shutdown)

    def _shutdown(self) -> None:
        """Закрыть все открытые user-request traces."""
        with self._request_traces_lock:
            traces = list(self._request_traces.values())
            self._request_traces.clear()
            self._request_last_seen.clear()
        for trace in traces:
            try:
                self.end_trace(trace, answer=None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("atexit end_trace failed: %s", exc)
        # Финальный flush Langfuse — гарантирует доставку.
        if self._langfuse_enabled and self._langfuse_client is not None:
            try:
                self._langfuse_client.flush()
            except Exception as exc:  # noqa: BLE001
                logger.debug("atexit langfuse flush failed: %s", exc)

    def _cleanup_stale_traces(self) -> None:
        """Закрыть traces, которые не получали новых tool вызовов > TTL.

        Защита от утечек если ouroboros task завершился без сигнала нам
        (e.g., ошибка LLM, exit пользователя в чате). Вызывается перед
        каждым новым get_or_create.
        """
        now = time.monotonic()
        stale: list[tuple[str, Trace]] = []
        with self._request_traces_lock:
            for req_id, last in list(self._request_last_seen.items()):
                if now - last > self._request_ttl_sec:
                    trace = self._request_traces.pop(req_id, None)
                    self._request_last_seen.pop(req_id, None)
                    if trace is not None:
                        stale.append((req_id, trace))
        for req_id, trace in stale:
            logger.info("observability: closing stale trace for request_id=%s", req_id)
            try:
                self.end_trace(trace, answer=None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("stale end_trace failed: %s", exc)

    # --- request-scoped traces (user-request = one trace) ---

    def get_or_create_trace_for_request(
        self,
        request_id: Optional[str],
        query: Optional[str],
        *,
        name: str = "user_request",
    ) -> tuple[Trace, bool]:
        """Получить trace для user-request или создать если его ещё нет.

        Args:
            request_id: ouroboros `ctx.task_id` (или fallback `current_chat_id`).
                Если None — каждый tool вызов получит новый trace
                (старое поведение, не группируется).
            query: первый user-query, привязанный к этому request. Передаётся
                только при первом вызове (создание trace); последующие вызовы
                с тем же request_id игнорируют параметр.
            name: имя root observation если создаём новый trace.

        Returns:
            (trace, is_new) — trace объект и флаг «trace создан этим вызовом»
            (для логирования).
        """
        self._cleanup_stale_traces()

        if not request_id:
            # Без request_id — каждый tool в своём trace (legacy fallback).
            return self.start_trace(query=query, name=name), True

        with self._request_traces_lock:
            existing = self._request_traces.get(request_id)
            if existing is not None:
                self._request_last_seen[request_id] = time.monotonic()
                return existing, False

        # Создаём новый — query берётся из первого tool вызова.
        trace = self.start_trace(query=query, name=name)
        with self._request_traces_lock:
            self._request_traces[request_id] = trace
            self._request_last_seen[request_id] = time.monotonic()
        logger.info(
            "observability: opened trace for request_id=%s (trace_id=%s)",
            request_id,
            trace.trace_id,
        )
        return trace, True

    def close_trace_for_request(
        self, request_id: str, *, answer: Optional[str] = None
    ) -> None:
        """Закрыть trace связанный с request_id (если есть).

        Используется когда внешний код (e.g., loop hook) знает что user-request
        завершён. Если не вызвать явно — trace закроется по TTL или atexit.
        """
        if not request_id:
            return
        with self._request_traces_lock:
            trace = self._request_traces.pop(request_id, None)
            self._request_last_seen.pop(request_id, None)
        if trace is not None:
            self.end_trace(trace, answer=answer)

    # --- public API ---

    def start_trace(
        self,
        query: Optional[str] = None,
        *,
        name: str = "analyst_request",
    ) -> Trace:
        """Открыть top-level trace. Не пишет в JSONL сразу — только при end_trace.

        В Langfuse 4.x trace = root span/observation. Trace_id генерируется
        SDK через `create_trace_id()`, и все вложенные observations связываются
        через `trace_context={"trace_id": ...}`.

        Args:
            query: исходный пользовательский запрос (input root observation).
            name: имя top-level trace в Langfuse. По умолчанию "analyst_request"
                для analyst_query tool. Для rag_search / web_search tool
                entry points — соответствующее имя.
        """
        # Langfuse 4.x требует валидный OTel trace_id (16 bytes hex). Используем
        # client.create_trace_id если SDK enabled, иначе обычный UUID.
        if self._langfuse_enabled and self._langfuse_client is not None:
            try:
                lf_trace_id = self._langfuse_client.create_trace_id()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Langfuse create_trace_id failed: %s", exc)
                lf_trace_id = str(uuid.uuid4())
        else:
            lf_trace_id = str(uuid.uuid4())

        trace = Trace(
            trace_id=lf_trace_id,
            started_at=time.monotonic(),
            ts_iso=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            query=query,
        )
        # Открываем root observation в Langfuse, привязываем к нашему trace_id.
        if self._langfuse_enabled and self._langfuse_client is not None:
            try:
                root_obs = self._langfuse_client.start_observation(
                    trace_context={"trace_id": lf_trace_id},
                    name=name,
                    as_type="span",
                    input={"query": query},
                )
                self._langfuse_traces[lf_trace_id] = root_obs
            except Exception as exc:  # noqa: BLE001
                logger.warning("Langfuse root observation start failed: %s", exc)
        return trace

    def end_trace(self, trace: Trace, answer: Optional[str] = None) -> None:
        """Записать summary-строку (kind=trace) в JSONL + закрыть Langfuse root."""
        trace.answer = answer
        total_ms = int((time.monotonic() - trace.started_at) * 1000)

        record = {
            "kind": "trace",
            "ts": trace.ts_iso,
            "trace_id": trace.trace_id,
            "query": trace.query,
            "total_latency_ms": total_ms,
            "total_cost_usd": round(trace.total_cost_usd, 8) if trace.total_cost_usd else 0.0,
            "span_count": trace.span_count,
            "status": trace.status,
        }
        if trace.status == "error" and trace.error_node:
            record["error_node"] = trace.error_node

        self._write_jsonl(record)

        # Langfuse — закрываем root observation + flush.
        if self._langfuse_enabled:
            try:
                root_obs = self._langfuse_traces.pop(trace.trace_id, None)
                if root_obs is not None:
                    root_obs.update(
                        output={"answer": answer},
                        metadata={
                            "total_latency_ms": total_ms,
                            "total_cost_usd": record["total_cost_usd"],
                            "span_count": trace.span_count,
                            "status": trace.status,
                        },
                        level="ERROR" if trace.status == "error" else "DEFAULT",
                    )
                    root_obs.end()
                if self._langfuse_client is not None:
                    self._langfuse_client.flush()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Langfuse trace end failed: %s", exc)

    def start_span(
        self,
        node: str,
        trace: Trace,
        input_data: Optional[dict[str, Any]] = None,
        as_type: str = "span",
    ) -> Span:
        """Открыть span. Не пишет в JSONL — запись только при end_span.

        as_type:
            "span"       — обычный узел (rule-based, validation, retrieval).
            "generation" — LLM-вызов; в Langfuse UI отрисовывается как chat
                           message с input/output, model, tokens, cost.
        """
        span = Span(
            trace_id=trace.trace_id,
            span_id=self.next_span_id(),
            node=node,
            started_at=time.monotonic(),
            ts_iso=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            input=self._truncate(input_data) if input_data else None,
        )
        if self._langfuse_enabled and self._langfuse_client is not None:
            try:
                # ВАЖНО: чтобы observation стал child (а не отдельным root в
                # trace), создаём через `parent.start_observation(...)`, не
                # `client.start_observation(trace_context=...)`. Иначе
                # Langfuse UI рисует каждый observation как самостоятельный
                # trace с дублирующимся trace_id.
                #
                # Hierarchy:
                #   parent = текущий открытый _current_span (если есть, для
                #     вложенности узлов внутри analyst_query span);
                #   иначе  = root observation для trace (ctx-grouped user-request);
                #   иначе  = client.start_observation с trace_context (top-level).
                parent_span = _current_span.get()
                parent_lf = (
                    getattr(parent_span, "_lf_span", None)
                    if parent_span is not None
                    else None
                )
                if parent_lf is None:
                    parent_lf = self._langfuse_traces.get(trace.trace_id)

                if parent_lf is not None:
                    lf_span = parent_lf.start_observation(
                        name=node,
                        as_type=as_type,
                        input=input_data,
                    )
                else:
                    # Top-level (нет ни parent span'а ни registered root) —
                    # привязываем к trace_id напрямую (legacy fallback).
                    lf_span = self._langfuse_client.start_observation(
                        trace_context={"trace_id": trace.trace_id},
                        name=node,
                        as_type=as_type,
                        input=input_data,
                    )
                setattr(span, "_lf_span", lf_span)
                setattr(span, "_lf_as_type", as_type)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Langfuse span start failed for node=%s: %s", node, exc)
        return span

    def end_span(
        self,
        span: Span,
        *,
        status: str = "ok",
        output_data: Optional[dict[str, Any]] = None,
        error: Optional[BaseException] = None,
        trace: Optional[Trace] = None,
    ) -> None:
        """Закрыть span: latency, статус, запись в JSONL + Langfuse."""
        latency_ms = int((time.monotonic() - span.started_at) * 1000)
        span.status = status
        if output_data is not None:
            span.output = self._truncate(output_data)
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

        # JSONL record.
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

        # Langfuse end — закрыть observation. 4.x API: update() для метаданных,
        # потом end(). Для as_type="generation" доступны usage_details +
        # cost_details — отдельные структуры, не metadata.
        lf_span = getattr(span, "_lf_span", None)
        lf_as_type = getattr(span, "_lf_as_type", "span")
        if lf_span is not None:
            try:
                metadata = {
                    "latency_ms": latency_ms,
                    "status": status,
                }
                update_kwargs: dict[str, Any] = {
                    "metadata": metadata,
                    "level": "ERROR" if status == "error" else "DEFAULT",
                }
                if output_data is not None:
                    update_kwargs["output"] = output_data
                if span.error is not None:
                    update_kwargs["status_message"] = span.error["message"]

                if lf_as_type == "generation":
                    # LLM-узел: model + tokens + cost через нативные поля.
                    if span.model:
                        update_kwargs["model"] = span.model
                    usage_details: dict[str, int] = {}
                    if span.prompt_tokens is not None:
                        usage_details["input"] = int(span.prompt_tokens)
                    if span.completion_tokens is not None:
                        usage_details["output"] = int(span.completion_tokens)
                    if usage_details:
                        update_kwargs["usage_details"] = usage_details
                    if span.cost_usd is not None:
                        update_kwargs["cost_details"] = {"total": float(span.cost_usd)}
                else:
                    # Обычный span — пишем в metadata, чтобы видеть в UI.
                    if span.model:
                        metadata["model"] = span.model
                    if span.prompt_tokens is not None:
                        metadata["prompt_tokens"] = span.prompt_tokens
                    if span.completion_tokens is not None:
                        metadata["completion_tokens"] = span.completion_tokens
                    if span.cost_usd is not None:
                        metadata["cost_usd"] = span.cost_usd

                lf_span.update(**update_kwargs)
                lf_span.end()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Langfuse span end failed for node=%s: %s", span.node, exc)


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
    """Reset singleton — только для unit-тестов."""
    global _GLOBAL_TRACER
    with _GLOBAL_LOCK:
        if _GLOBAL_TRACER is not None and _GLOBAL_TRACER._jsonl_handle is not None:
            try:
                _GLOBAL_TRACER._jsonl_handle.close()
            except OSError:
                pass
        _GLOBAL_TRACER = None


# =============================================================================
# log_llm_usage — вызывается из LLM-узла после chat-call
# =============================================================================


def log_llm_usage(
    usage: Optional[dict[str, Any]],
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> None:
    """Прикрепить tokens/cost к текущему span'у.

    Args:
        usage: ouroboros usage dict (prompt_tokens / completion_tokens / cost
               / resolved_model / provider) или langchain usage_metadata
               (input_tokens / output_tokens). Если None — no-op.
        model: имя модели, если не указано в usage.
        provider: провайдер ('hydra' / 'gigachat' / ...), если не в usage.
    """
    if usage is None:
        return

    span = _current_span.get()
    if span is None:
        # LLM-вызов вне @observe-узла. Логировать некуда. Это не критично —
        # бывает в unit-тестах. В production-графе все LLM-вызовы под узлами.
        logger.debug("log_llm_usage called outside span context — ignored")
        return

    # ouroboros: usage["prompt_tokens"] / "completion_tokens" / "resolved_model" / "provider" / "cost"
    # langchain: usage["input_tokens"] / "output_tokens"
    prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
    completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
    cached_tokens = usage.get("cached_tokens") or 0

    if prompt_tokens is not None:
        span.prompt_tokens = int(prompt_tokens)
    if completion_tokens is not None:
        span.completion_tokens = int(completion_tokens)

    resolved_model = model or usage.get("resolved_model") or usage.get("model")
    if resolved_model:
        # Snip префикс провайдера для display (Langfuse UI показывает model
        # отдельным полем, "openai-compatible/kimi-k2p6" нечитаемо). Полное имя
        # сохраним в metadata через provider, а stripped — в model.
        from nefteboros.observability.cost import _strip_provider_prefix

        span.model = _strip_provider_prefix(str(resolved_model))
    resolved_provider = provider or usage.get("provider")
    if resolved_provider:
        span.provider = str(resolved_provider)

    # Cost: сначала из usage (если ouroboros / провайдер посчитал), иначе compute_cost.
    cost = usage.get("cost")
    if cost is None and span.model and prompt_tokens is not None and completion_tokens is not None:
        cost = compute_cost(
            span.model,
            int(prompt_tokens),
            int(completion_tokens),
            int(cached_tokens),
        )
    if cost is not None:
        span.cost_usd = float(cost)


__all__ = [
    "Span",
    "Trace",
    "get_tracer",
    "log_llm_usage",
    "_current_span",
    "_current_trace",
    "_reset_for_tests",
]
