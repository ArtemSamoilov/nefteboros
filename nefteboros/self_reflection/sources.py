"""Источники трейсов для рефлексии — READ-ONLY.

См. ADR-0029-self-reflection.

Два источника:

- `JsonlTraceSource` — **первичный, всегда доступен**. Читает offline
  JSON-trace'ы, которые observability пишет в `metrics/runs/<ts>/trace.jsonl`
  (см. `nefteboros/observability/tracer.py`). Не зависит от облака. Богатство:
  структурное (узлы/статусы/latency/cost/query) — текст ответа live-tracer НЕ
  хранит (усекает до compact-метаданных), поэтому `TraceView.answer` тут `None`.
- `LangfuseTraceSource` — **best-effort**, если заданы ключи Langfuse. Langfuse
  через `@observe` auto-capture хранит полный input/output → даёт текст ответа
  (`answer`) и включает контентные детекторы (refusal/citation). На ЛЮБОЙ сбой
  (нет SDK / нет сети / иной API) — graceful возврат `[]` и откат на JSONL.

**Изоляция (ADR-0027):** оба источника читают ТОЛЬКО трейсы. Они НЕ касаются
`data/logs/chat.jsonl`, истории чата, analyst-контекста. Чтение односторонее.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import pathlib
from typing import Any, Optional

from nefteboros.self_reflection.schema import TraceView

logger = logging.getLogger(__name__)

# Узлы, чей output может нести текст финального ответа (для источников, которые
# хранят контент — Langfuse/sample). Для live JSONL output усечён → answer=None.
_ANSWER_NODES = ("synthesize", "analyst", "user_request", "rag_search", "web_search")


def _repo_root() -> pathlib.Path:
    cwd = pathlib.Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return cwd


# =============================================================================
# JSONL source (первичный)
# =============================================================================


class JsonlTraceSource:
    """Читает `metrics/runs/*/trace.jsonl` (или явные пути) read-only."""

    def __init__(
        self,
        *,
        run_root: Optional[pathlib.Path] = None,
        explicit_paths: Optional[list[pathlib.Path]] = None,
    ) -> None:
        self._explicit = explicit_paths
        if run_root is not None:
            self._run_root = pathlib.Path(run_root)
        else:
            env = os.environ.get("OBSERVABILITY_RUN_DIR", "").strip()
            # OBSERVABILITY_RUN_DIR указывает на ОДИН run-dir; его родитель —
            # каталог всех прогонов. Иначе — <repo>/metrics/runs.
            if env:
                self._run_root = pathlib.Path(env).parent
            else:
                self._run_root = _repo_root() / "metrics" / "runs"

    def _trace_files(self) -> list[pathlib.Path]:
        if self._explicit is not None:
            return [p for p in self._explicit if p.exists()]
        pattern = str(self._run_root / "*" / "trace.jsonl")
        files = [pathlib.Path(p) for p in glob.glob(pattern)]
        # самые свежие прогоны первыми
        files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return files

    def recent_traces(self, limit: int = 50) -> list[TraceView]:
        """Собрать до `limit` последних трейсов (группировка по trace_id)."""
        views: list[TraceView] = []
        for f in self._trace_files():
            if len(views) >= limit:
                break
            try:
                views.extend(_views_from_file(f))
            except OSError as exc:
                logger.debug("cannot read trace file %s: %s", f, exc)
        # стабильный порядок: по ts убыв., затем обрезаем
        views.sort(key=lambda v: v.ts or "", reverse=True)
        return views[:limit]


def _views_from_file(path: pathlib.Path) -> list[TraceView]:
    groups: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            tid = str(rec.get("trace_id") or "")
            if not tid:
                continue
            g = groups.setdefault(tid, {"summary": None, "spans": []})
            if rec.get("kind") == "trace":
                g["summary"] = rec
            elif rec.get("kind") == "span":
                g["spans"].append(rec)
    return [_build_view(tid, g) for tid, g in groups.items()]


def _build_view(trace_id: str, g: dict[str, Any]) -> TraceView:
    summary: Optional[dict[str, Any]] = g.get("summary")
    spans: list[dict[str, Any]] = g.get("spans", [])

    nodes = [str(s.get("node")) for s in spans if s.get("node")]
    error_nodes = [
        str(s.get("node")) for s in spans if s.get("status") == "error" and s.get("node")
    ]

    status = "ok"
    if summary and summary.get("status"):
        status = str(summary["status"])
    elif error_nodes:
        status = "error"

    latency = None
    cost = None
    ts = None
    query = None
    if summary:
        latency = summary.get("total_latency_ms")
        cost = summary.get("total_cost_usd")
        ts = summary.get("ts")
        query = summary.get("query")
    if latency is None:
        s = sum(int(sp.get("latency_ms") or 0) for sp in spans)
        latency = s or None
    if cost is None:
        c = sum(float(sp.get("cost_usd") or 0.0) for sp in spans)
        cost = c or None
    if ts is None and spans:
        ts = min((str(sp.get("ts") or "") for sp in spans), default=None) or None

    # answer — best-effort: явное поле (Langfuse/sample) или строковый span-output.
    # Для live JSONL output усечён до dict → остаётся None (это ожидаемо).
    answer = None
    if summary and isinstance(summary.get("answer"), str):
        answer = summary["answer"]
    if answer is None:
        candidates: list[str] = []
        for sp in spans:
            out = sp.get("output")
            if isinstance(out, str) and out.strip():
                candidates.append(out)
            elif isinstance(out, dict) and isinstance(out.get("answer"), str):
                candidates.append(out["answer"])
        if candidates:
            answer = max(candidates, key=len)

    span_count = (
        int(summary["span_count"])
        if summary and summary.get("span_count") is not None
        else len(spans)
    )

    return TraceView(
        trace_id=trace_id,
        ts=ts,
        query=query,
        answer=answer,
        status=status,
        latency_ms=int(latency) if latency is not None else None,
        cost_usd=float(cost) if cost is not None else None,
        nodes=nodes,
        error_nodes=error_nodes,
        span_count=span_count,
    )


# =============================================================================
# Langfuse source (best-effort)
# =============================================================================


def _langfuse_keys_present() -> bool:
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    )


class LangfuseTraceSource:
    """Тянет последние трейсы из Langfuse через SDK. Best-effort: любой сбой →
    `[]` (вызывающий откатится на JSONL). Не тестируется без ключей —
    защищено try/except по всему пути."""

    def recent_traces(self, limit: int = 50) -> list[TraceView]:
        try:
            from langfuse import get_client  # type: ignore

            client = get_client()
            # Langfuse 4.x: client.api.trace.list(...) → page с .data
            page = client.api.trace.list(limit=limit)
            raw = getattr(page, "data", None) or []
        except Exception as exc:  # noqa: BLE001
            logger.info("Langfuse fetch unavailable (%s) — fallback to JSONL", exc)
            return []

        views: list[TraceView] = []
        for t in raw:
            try:
                views.append(self._to_view(t))
            except Exception:  # noqa: BLE001
                continue
        return views

    @staticmethod
    def _to_view(t: Any) -> TraceView:
        def g(obj: Any, name: str) -> Any:
            if isinstance(obj, dict):
                return obj.get(name)
            return getattr(obj, name, None)

        tid = str(g(t, "id") or g(t, "trace_id") or "")
        inp = g(t, "input")
        out = g(t, "output")
        query = inp if isinstance(inp, str) else (json.dumps(inp, ensure_ascii=False) if inp else None)
        answer = out if isinstance(out, str) else (json.dumps(out, ensure_ascii=False) if out else None)
        latency = g(t, "latency")
        return TraceView(
            trace_id=tid,
            ts=str(g(t, "timestamp") or "") or None,
            query=query,
            answer=answer,
            status="error" if g(t, "level") == "ERROR" else "ok",
            latency_ms=int(float(latency) * 1000) if latency else None,
            cost_usd=g(t, "totalCost") or g(t, "total_cost"),
            nodes=[],
            error_nodes=[],
            span_count=0,
        )


# =============================================================================
# Selector
# =============================================================================


def load_recent_traces(
    limit: int = 50,
    *,
    run_root: Optional[pathlib.Path] = None,
    explicit_paths: Optional[list[pathlib.Path]] = None,
    prefer_langfuse: bool = True,
) -> tuple[list[TraceView], str]:
    """Вернуть `(traces, source_name)`. Langfuse если ключи заданы и отдаёт
    данные, иначе JSONL. `explicit_paths` форсит JSONL из конкретных файлов
    (для демо/тестов)."""
    if explicit_paths is not None:
        src = JsonlTraceSource(explicit_paths=explicit_paths)
        return src.recent_traces(limit), "jsonl"
    if prefer_langfuse and _langfuse_keys_present():
        traces = LangfuseTraceSource().recent_traces(limit)
        if traces:
            return traces, "langfuse"
    src = JsonlTraceSource(run_root=run_root)
    return src.recent_traces(limit), "jsonl"


__all__ = [
    "JsonlTraceSource",
    "LangfuseTraceSource",
    "load_recent_traces",
]
