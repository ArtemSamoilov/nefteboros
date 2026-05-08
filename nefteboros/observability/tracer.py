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
    """Один узел графа в трейсе. Mutable — поля заполняются по ходу выполнения.

    Разделение full / compact:
    - `input` / `output` — compact для JSON-trace (truncated для атомарного
      append <PIPE_BUF). Используется для self-contained debug-файла на диске.
    - `input_full` / `output_full` — полный объект для Langfuse UI (там нет
      ограничения на размер observation, оценщику нужны полные ответы).
    """

    trace_id: str
    span_id: int
    node: str
    started_at: float  # monotonic time для latency
    ts_iso: str  # человекочитаемый
    parent_span_id: Optional[int] = None
    status: str = "ok"  # ok | error | skipped
    input: Optional[Any] = None  # compact, truncated — для JSON
    output: Optional[Any] = None  # compact, truncated — для JSON
    input_full: Optional[Any] = None  # full content — для Langfuse
    output_full: Optional[Any] = None  # full content — для Langfuse
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
    answer_full: Optional[Any] = None  # для Langfuse UI (полный финал)
    span_count: int = 0
    status: str = "ok"
    error_node: Optional[str] = None
    total_cost_usd: float = 0.0
    # Sessions / user attribution в Langfuse (см. ADR-0025).
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


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

        ВНИМАНИЕ (refactor 2026-05-08): инициализация всегда отключена.
        Реальная отправка в Langfuse идёт через native `@langfuse.observe`
        декоратор в `nefteboros/observability/__init__.py` — он правильно
        управляет OTel context'ом, иерархией и content auto-capture. Эта
        функция оставлена для совместимости (атрибут `_langfuse_client`
        проверяется в legacy-блоках start_span/end_span/start_trace/end_trace
        — они скипаются при `_langfuse_enabled=False`). См. ADR-0025.
        """
        # SDK инициализация ниже отключена — все Langfuse-вызовы идут через
        # langfuse.observe в __init__.py.
        return
        # ----- legacy code ниже не выполняется -----
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
    def _serialize(value: Any) -> Any:
        """Превратить произвольное значение в JSON-совместимый объект.

        Важно для Langfuse: SDK сериализует через json.dumps, и pydantic-объекты
        (Intent, ForecastResult, Citation) ломают сериализацию default'ом.
        Делаем dump через `model_dump` (pydantic v2), `_asdict` (namedtuple),
        `__dict__` (dataclass / regular). Циклы не покрываем — domain объекты
        в нашем графе плоские.
        """
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        # pydantic v2 BaseModel
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump(mode="json")
            except Exception:  # noqa: BLE001
                pass
        # pydantic v1 / namedtuple
        if hasattr(value, "_asdict"):
            try:
                return _Tracer._serialize(value._asdict())
            except Exception:  # noqa: BLE001
                pass
        # dataclass без model_dump
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            try:
                return {f.name: _Tracer._serialize(getattr(value, f.name)) for f in dataclasses.fields(value)}
            except Exception:  # noqa: BLE001
                pass
        # Enum
        if hasattr(value, "value") and hasattr(value, "name") and type(value).__bases__ and any(
            "Enum" in b.__name__ for b in type(value).__mro__
        ):
            return value.value
        if isinstance(value, dict):
            return {str(k): _Tracer._serialize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_Tracer._serialize(v) for v in value]
        # datetime / pathlib / прочее — через str()
        return str(value)

    @staticmethod
    def _truncate(value: Any) -> Any:
        """Truncate string-fields в input/output до TRUNCATE_THRESHOLD bytes.

        Применяется ТОЛЬКО для JSON-trace на диске (concurrency-safe append
        требует <PIPE_BUF). Для Langfuse используем `_serialize` без truncate —
        UI оценщика должен видеть полный ответ агента.
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
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
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
            session_id: chat session для группировки в Langfuse UI.
            user_id: атрибуция к пользователю.

        Returns:
            (trace, is_new) — trace объект и флаг «trace создан этим вызовом»
            (для логирования).
        """
        self._cleanup_stale_traces()

        if not request_id:
            # Без request_id — каждый tool в своём trace (legacy fallback).
            return (
                self.start_trace(
                    query=query, name=name, session_id=session_id, user_id=user_id
                ),
                True,
            )

        with self._request_traces_lock:
            existing = self._request_traces.get(request_id)
            if existing is not None:
                self._request_last_seen[request_id] = time.monotonic()
                return existing, False

        # Создаём новый — query берётся из первого tool вызова.
        trace = self.start_trace(
            query=query, name=name, session_id=session_id, user_id=user_id
        )
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
        self,
        request_id: str,
        *,
        answer: Optional[str] = None,
        answer_full: Optional[Any] = None,
    ) -> None:
        """Закрыть trace связанный с request_id (если есть).

        Используется когда внешний код (e.g., loop hook) знает что user-request
        завершён. Если не вызвать явно — trace закроется по TTL или atexit.

        Args:
            answer: компактный preview для JSON-trace.
            answer_full: полный финал для Langfuse UI.
        """
        if not request_id:
            return
        with self._request_traces_lock:
            trace = self._request_traces.pop(request_id, None)
            self._request_last_seen.pop(request_id, None)
        if trace is not None:
            self.end_trace(trace, answer=answer, answer_full=answer_full)

    # --- public API ---

    def start_trace(
        self,
        query: Optional[str] = None,
        *,
        name: str = "analyst_request",
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
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
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
        )
        # Открываем root observation в Langfuse, привязываем к нашему trace_id.
        if self._langfuse_enabled and self._langfuse_client is not None:
            try:
                root_obs = self._langfuse_client.start_observation(
                    trace_context={"trace_id": lf_trace_id},
                    name=name,
                    as_type="span",
                    input={"query": query} if query else None,
                )
                # Trace-level метаданные через update_trace(...). Без него
                # trace.name в Langfuse UI берётся из последнего span'а
                # (на проде получалось «forecast_call» вместо «user_request»).
                try:
                    if hasattr(root_obs, "update_trace"):
                        ut_kwargs: dict[str, Any] = {"name": name}
                        if session_id:
                            ut_kwargs["session_id"] = session_id
                        if user_id:
                            ut_kwargs["user_id"] = user_id
                        if metadata:
                            ut_kwargs["metadata"] = metadata
                        if query:
                            ut_kwargs["input"] = {"query": query}
                        root_obs.update_trace(**ut_kwargs)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("update_trace failed: %s", exc)
                self._langfuse_traces[lf_trace_id] = root_obs
            except Exception as exc:  # noqa: BLE001
                logger.warning("Langfuse root observation start failed: %s", exc)
        return trace

    def end_trace(
        self,
        trace: Trace,
        answer: Optional[str] = None,
        *,
        answer_full: Optional[Any] = None,
    ) -> None:
        """Записать summary-строку (kind=trace) в JSONL + закрыть Langfuse root.

        Args:
            answer: компактный preview для JSON-trace summary.
            answer_full: полный объект для Langfuse UI (markdown-ответ агента,
                JSON со всеми citations, etc). Если None, fallback на answer.
        """
        trace.answer = answer
        if answer_full is not None:
            trace.answer_full = self._serialize(answer_full)
        elif answer is not None:
            trace.answer_full = answer
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
                    final_output = (
                        trace.answer_full
                        if trace.answer_full is not None
                        else ({"answer": answer} if answer else None)
                    )
                    root_obs.update(
                        output=final_output,
                        metadata={
                            "total_latency_ms": total_ms,
                            "total_cost_usd": record["total_cost_usd"],
                            "span_count": trace.span_count,
                            "status": trace.status,
                        },
                        level="ERROR" if trace.status == "error" else "DEFAULT",
                    )
                    # Trace-level output (для UI отображения «final answer»):
                    if hasattr(root_obs, "update_trace") and final_output is not None:
                        try:
                            root_obs.update_trace(output=final_output)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("update_trace(output) failed: %s", exc)
                    root_obs.end()
                if self._langfuse_client is not None:
                    self._langfuse_client.flush()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Langfuse trace end failed: %s", exc)

    def start_span(
        self,
        node: str,
        trace: Trace,
        input_data: Optional[Any] = None,
        as_type: str = "span",
        input_compact: Optional[Any] = None,
    ) -> Span:
        """Открыть span. Не пишет в JSONL — запись только при end_span.

        as_type:
            "span"       — обычный узел (rule-based, validation, retrieval).
            "generation" — LLM-вызов; в Langfuse UI отрисовывается как chat
                           message с input/output, model, tokens, cost.

        Args:
            input_data: полный объект (для Langfuse UI). Если pydantic /
                dataclass — прогоняется через `_serialize` чтобы json.dumps
                в SDK не упал.
            input_compact: компактная версия для JSON-trace на диске. Если
                None — берётся `_truncate(input_data)`.
        """
        full_input = self._serialize(input_data) if input_data is not None else None
        compact_input = (
            self._truncate(input_compact)
            if input_compact is not None
            else (self._truncate(full_input) if full_input is not None else None)
        )
        span = Span(
            trace_id=trace.trace_id,
            span_id=self.next_span_id(),
            node=node,
            started_at=time.monotonic(),
            ts_iso=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            input=compact_input,
            input_full=full_input,
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

                # ВАЖНО (Langfuse SDK 4.x): `parent.start_observation(name=..)`
                # НЕ передаёт name/input в Langfuse — поля приходят как None
                # (проверено через legacy.observations_v1 endpoint).
                # Используем `client.start_observation(trace_context={"trace_id":...,
                # "parent_span_id": parent.id})` — это явно указывает parent
                # и сохраняет name/input/output корректно.
                if parent_lf is not None:
                    parent_id = getattr(parent_lf, "id", None)
                    if parent_id:
                        lf_span = self._langfuse_client.start_observation(
                            trace_context={
                                "trace_id": trace.trace_id,
                                "parent_span_id": parent_id,
                            },
                            name=node,
                            as_type=as_type,
                            input=full_input,
                        )
                    else:
                        # parent без .id — fallback через метод
                        lf_span = parent_lf.start_observation(
                            name=node, as_type=as_type, input=full_input
                        )
                else:
                    # Top-level (нет ни parent span'а ни registered root) —
                    # привязываем к trace_id напрямую (legacy fallback).
                    lf_span = self._langfuse_client.start_observation(
                        trace_context={"trace_id": trace.trace_id},
                        name=node,
                        as_type=as_type,
                        input=full_input,
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
        output_data: Optional[Any] = None,
        output_compact: Optional[Any] = None,
        error: Optional[BaseException] = None,
        trace: Optional[Trace] = None,
    ) -> None:
        """Закрыть span: latency, статус, запись в JSONL + Langfuse.

        Args:
            output_data: полный объект — для Langfuse UI.
            output_compact: компактная версия — для JSON-trace на диске. Если
                None, берётся `_truncate(output_data)`.
        """
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
                if span.output_full is not None:
                    update_kwargs["output"] = span.output_full
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
    """Прикрепить tokens/cost к текущему span'у (JSON-trace + Langfuse).

    Внутренне:
    1. Заполнить поля Span dataclass для JSON-trace.
    2. Если запущен внутри `@observe(as_type="generation")` Langfuse-span'а —
       прокинуть usage_details / cost_details через `client.update_current_generation`.

    Args:
        usage: ouroboros usage dict (prompt_tokens / completion_tokens / cost
               / resolved_model / provider) или langchain usage_metadata
               (input_tokens / output_tokens). None → no-op.
        model: имя модели, если не указано в usage.
        provider: провайдер ('hydra' / 'gigachat' / ...), если не в usage.
    """
    if usage is None:
        return

    span = _current_span.get()
    # ouroboros: prompt_tokens / completion_tokens / resolved_model / provider / cost
    # langchain: input_tokens / output_tokens
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

    # 1. JSON-trace: записать в Span dataclass для записи в trace.jsonl.
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
    else:
        logger.debug("log_llm_usage: no current span (JSON-trace skipped)")

    # 2. Langfuse SDK: прокинуть в текущий generation observation.
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
        pass  # Langfuse not installed — JSON-trace already записан.
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
