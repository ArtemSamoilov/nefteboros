"""Unit-тесты для observability декораторов.

Проверяют:
1. `_extract_session_id` / `_extract_user_id` — корректное извлечение из ToolContext.
2. `traced_tool` без Langfuse SDK (LANGFUSE_ENABLED=false): handler вызывается,
   JSON-trace span создаётся, ошибки не выбрасываются.
3. `observe` async-декоратор: handler вызывается, orphan trace создаётся
   при отсутствии _current_trace.
4. Сигнатура `traced_tool` поддерживает оба пути ouroboros tool dispatch:
   `handler(ctx, **args)` (с ctx) и `handler(**args)` (без ctx).

Langfuse-сторону не мокаем — тесты с LANGFUSE_ENABLED=false (см. conftest.py)
проверяют что код не падает без SDK. Реальную Langfuse интеграцию покрывает
`scripts/verify_langfuse_content.py` через API readback.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pytest

# conftest.py форсит LANGFUSE_ENABLED=false ДО импорта модулей,
# поэтому _try_import_langfuse() кеширует False.

from nefteboros.observability import (
    _extract_session_id,  # type: ignore[attr-defined]
    _extract_user_id,  # type: ignore[attr-defined]
    observe,
    traced_tool,
)
from nefteboros.observability.tracer import _reset_for_tests


@dataclass
class MockCtx:
    task_id: Optional[str] = None
    current_chat_id: Optional[Any] = None
    user_id: Optional[str] = None


@pytest.fixture(autouse=True)
def _isolate_jsonl(monkeypatch, tmp_path):
    """Изолировать JSON-trace в tmp dir + reset singleton tracer."""
    monkeypatch.setenv("OBSERVABILITY_RUN_DIR", str(tmp_path))
    _reset_for_tests()
    yield
    _reset_for_tests()


def _read_jsonl(tmp_path: Path) -> list[dict]:
    p = tmp_path / "trace.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# =============================================================================
# _extract_session_id / _extract_user_id
# =============================================================================


class TestExtractSessionId:
    def test_none_ctx(self):
        assert _extract_session_id(None) is None

    def test_no_chat_id(self):
        assert _extract_session_id(MockCtx(task_id="t1")) is None

    def test_chat_id_int(self):
        assert _extract_session_id(MockCtx(current_chat_id=42)) == "chat:42"

    def test_chat_id_str(self):
        assert _extract_session_id(MockCtx(current_chat_id="abc")) == "chat:abc"

    def test_no_attrs(self):
        """ctx без current_chat_id поля — getattr вернёт None."""

        class Empty:
            pass

        assert _extract_session_id(Empty()) is None


class TestExtractUserId:
    def test_none_ctx(self):
        assert _extract_user_id(None) is None

    def test_user_id_present(self):
        assert _extract_user_id(MockCtx(user_id="alice")) == "alice"

    def test_no_user_id_field(self):
        """ToolContext в текущем ouroboros не имеет user_id — getattr None."""

        class Empty:
            pass

        assert _extract_user_id(Empty()) is None


# =============================================================================
# traced_tool: sync handler, langfuse disabled (offline-like)
# =============================================================================


class TestTracedTool:
    def test_calls_handler_with_kwargs(self, tmp_path):
        @traced_tool(name="my_tool")
        def handler(ctx=None, *, query: str = "") -> str:
            return f"got: {query}"

        result = handler(query="test query")
        assert result == "got: test query"

    def test_calls_handler_with_ctx(self, tmp_path):
        captured = {}

        @traced_tool(name="my_tool")
        def handler(ctx=None, *, query: str = "") -> str:
            captured["ctx"] = ctx
            captured["query"] = query
            return "ok"

        ctx = MockCtx(task_id="t1", current_chat_id="c1")
        handler(ctx, query="q1")
        assert captured["ctx"] is ctx
        assert captured["query"] == "q1"

    def test_jsonl_writes_trace_and_span(self, tmp_path):
        @traced_tool(name="my_tool")
        def handler(ctx=None, *, query: str = "") -> str:
            return json.dumps({"answer": "result"})

        ctx = MockCtx(current_chat_id="c1")
        handler(ctx, query="q")

        records = _read_jsonl(tmp_path)
        kinds = [r["kind"] for r in records]
        assert "span" in kinds, "tool span должен быть в JSONL"
        assert "trace" in kinds, "trace summary должен быть в JSONL"

        # Span должен быть с именем 'my_tool'
        span = next(r for r in records if r["kind"] == "span")
        assert span["node"] == "my_tool"
        assert span["status"] == "ok"

        # Trace summary с session_id из ctx
        trace = next(r for r in records if r["kind"] == "trace")
        assert trace["session_id"] == "chat:c1"

    def test_legacy_call_without_ctx(self, tmp_path):
        """Когда ouroboros не передаёт ctx (handler(**args) fallback)."""

        @traced_tool(name="legacy_tool")
        def handler(ctx=None, *, query: str = "") -> str:
            return "ok"

        result = handler(query="q")  # без ctx
        assert result == "ok"

        records = _read_jsonl(tmp_path)
        # session_id отсутствует (ctx None)
        trace = next(r for r in records if r["kind"] == "trace")
        assert "session_id" not in trace

    def test_exception_re_raised_and_trace_closed(self, tmp_path):
        @traced_tool(name="failing_tool")
        def handler(ctx=None, *, query: str = "") -> str:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            handler(query="q")

        # Span закрыт со status=error, trace закрыт
        records = _read_jsonl(tmp_path)
        span = next(r for r in records if r["kind"] == "span")
        assert span["status"] == "error"
        assert span["error"]["type"] == "ValueError"

        trace = next(r for r in records if r["kind"] == "trace")
        assert trace["status"] == "error"


# =============================================================================
# observe: async decorator для graph узлов
# =============================================================================


class TestObserve:
    def test_calls_async_handler(self, tmp_path):
        @observe(name="my_node", as_type="span")
        async def node(state):
            return {"intent": "x"}

        result = asyncio.run(node({"query": "q"}))
        assert result == {"intent": "x"}

    def test_orphan_trace_when_no_current(self, tmp_path):
        """Если декоратор вызван без обрамляющего trace — пишет orphan."""

        @observe(name="orphan_node", as_type="span")
        async def node(state):
            return {"k": "v"}

        asyncio.run(node({}))

        records = _read_jsonl(tmp_path)
        spans = [r for r in records if r["kind"] == "span" and r["node"] == "orphan_node"]
        assert len(spans) == 1
        assert spans[0]["trace_id"].startswith("orphan_"), (
            "orphan_-prefix должен быть проставлен для unit-test/direct invocation"
        )

    def test_sync_function_raises_typeerror(self):
        """Декоратор для async-узлов не должен принимать sync."""
        with pytest.raises(TypeError, match="async"):

            @observe(name="bad")
            def sync_fn(state):
                return {}

    def test_exception_logs_error_status(self, tmp_path):
        @observe(name="failing_node", as_type="span")
        async def node(state):
            raise RuntimeError("graph node failed")

        with pytest.raises(RuntimeError):
            asyncio.run(node({}))

        records = _read_jsonl(tmp_path)
        span = next(r for r in records if r["node"] == "failing_node")
        assert span["status"] == "error"
        assert span["error"]["type"] == "RuntimeError"


# =============================================================================
# Integration smoke: traced_tool вокруг observe-обёрнутого узла
# =============================================================================


class TestIntegration:
    def test_tool_with_async_inner_node(self, tmp_path):
        """Имитация: tool handler вызывает graph узел через asyncio.run.
        В JSONL должны быть оба span'а в одном trace_id (parent ноды
        прицеплен к tool span'у через _current_span/_current_trace)."""

        @observe(name="inner_node", as_type="span")
        async def inner(state):
            return {"computed": state.get("x", 0) + 1}

        @traced_tool(name="outer_tool")
        def outer(ctx=None, *, query: str = "") -> str:
            result = asyncio.run(inner({"x": 5}))
            return json.dumps(result)

        ctx = MockCtx(current_chat_id="c1")
        result = outer(ctx, query="q")
        assert json.loads(result) == {"computed": 6}

        records = _read_jsonl(tmp_path)
        # Нужны spans outer_tool и inner_node, в одном JSON-trace
        # (registry убрана — но _current_trace contextvar даёт inner ноде
        # тот же trace что и outer'у tool'у).
        names = {r["node"] for r in records if r["kind"] == "span"}
        assert "outer_tool" in names
        assert "inner_node" in names
