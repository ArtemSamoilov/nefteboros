"""Smoke-тесты для tool `web_search` в skill `neftegaz_analyst` (ADR-0022).

Mock'аем `nefteboros.search.WebSearcher` через sys.modules — handler
делает lazy import, так что подмена работает.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "neftegaz_analyst"


def _import_plugin_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_neftegaz_analyst_plugin",
        SKILL_DIR / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
# Input validation
# =============================================================================


def test_web_search_empty_query_returns_error() -> None:
    plugin = _import_plugin_module()
    raw = plugin._tool_web_search(query="")
    payload = json.loads(raw)
    assert payload == {"error": "query is empty"}


def test_web_search_too_long_query_returns_error() -> None:
    plugin = _import_plugin_module()
    raw = plugin._tool_web_search(query="а" * 2500)
    payload = json.loads(raw)
    assert "too long" in payload.get("error", "")


# =============================================================================
# Lazy-import behaviour — нет ключа / unavailable
# =============================================================================


def test_web_search_no_brave_key_returns_error(monkeypatch) -> None:
    """BRAVE_API_KEY не задан → handler возвращает осмысленный error."""
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    plugin = _import_plugin_module()
    raw = plugin._tool_web_search(query="brent latest news")
    payload = json.loads(raw)
    assert "error" in payload
    assert "BRAVE_API_KEY" in payload["error"]


# =============================================================================
# Happy path
# =============================================================================


@pytest.fixture
def fake_searcher_module():
    """Подменяет `nefteboros.search` на модуль с программируемым WebSearcher.

    Реальный модуль уже импортирован в conftest/др. тестах — мы перезаписываем
    его в sys.modules для целей этого теста, восстанавливая на teardown.
    """
    saved = sys.modules.get("nefteboros.search")
    yield_module = type(sys)("nefteboros.search")

    class FakeBraveError(RuntimeError):
        pass

    yield_module.BraveError = FakeBraveError
    sys.modules["nefteboros.search"] = yield_module
    try:
        yield yield_module, FakeBraveError
    finally:
        if saved is not None:
            sys.modules["nefteboros.search"] = saved
        else:
            sys.modules.pop("nefteboros.search", None)


def test_web_search_happy_path(fake_searcher_module) -> None:
    fake_module, _ = fake_searcher_module

    class _FakeHit:
        def __init__(self, title, url, hostname, tier, snippet, age=None, published=None):
            self.title = title
            self.url = url
            self.hostname = hostname
            self.tier = tier
            self.snippet = snippet
            self.age = age
            self.published = published

    class _FakeSearcher:
        def __init__(self, *_a, **_kw):
            pass

        @property
        def has_key(self) -> bool:
            return True

        def search(self, query, *, k=5, freshness="pw", tier_filter="all"):
            assert query == "что повлияет на цену Brent"
            assert k == 3
            assert freshness == "pd"
            assert tier_filter == "tier1"
            return [
                _FakeHit(
                    title="OPEC+ extends cuts",
                    url="https://www.reuters.com/article/opec-extends",
                    hostname="reuters.com",
                    tier="tier1",
                    snippet="OPEC+ producers agreed to extend production cuts...",
                    age="3 hours ago",
                    published="2026-05-07T05:00:00Z",
                )
            ]

    fake_module.WebSearcher = _FakeSearcher

    plugin = _import_plugin_module()
    raw = plugin._tool_web_search(
        query="что повлияет на цену Brent",
        freshness="pd",
        k=3,
        tier="tier1",
    )
    payload = json.loads(raw)

    assert "error" not in payload
    assert payload["query"] == "что повлияет на цену Brent"
    assert payload["k"] == 3
    assert payload["freshness"] == "pd"
    assert payload["tier_filter"] == "tier1"
    assert payload["total_returned"] == 1

    result = payload["results"][0]
    assert result["title"] == "OPEC+ extends cuts"
    assert result["hostname"] == "reuters.com"
    assert result["tier"] == "tier1"
    assert result["age"] == "3 hours ago"
    assert "https://www.reuters.com" in result["url"]


def test_web_search_invalid_freshness_falls_back(fake_searcher_module) -> None:
    fake_module, _ = fake_searcher_module
    captured = {}

    class _FakeSearcher:
        @property
        def has_key(self):
            return True

        def search(self, query, *, k=5, freshness="pw", tier_filter="all"):
            captured["freshness"] = freshness
            return []

    fake_module.WebSearcher = lambda: _FakeSearcher()

    plugin = _import_plugin_module()
    raw = plugin._tool_web_search(query="x", freshness="garbage")
    assert "error" not in json.loads(raw)
    assert captured["freshness"] == "pw"


def test_web_search_clamps_k(fake_searcher_module) -> None:
    fake_module, _ = fake_searcher_module
    captured = {}

    class _FakeSearcher:
        @property
        def has_key(self):
            return True

        def search(self, query, *, k=5, **kw):
            captured["k"] = k
            return []

    fake_module.WebSearcher = lambda: _FakeSearcher()

    plugin = _import_plugin_module()
    raw = plugin._tool_web_search(query="x", k=999)
    assert json.loads(raw)["k"] == 10
    assert captured["k"] == 10


def test_web_search_handles_brave_error(fake_searcher_module) -> None:
    fake_module, FakeBraveError = fake_searcher_module

    class _FakeSearcher:
        @property
        def has_key(self):
            return True

        def search(self, *a, **kw):
            raise FakeBraveError("rate limit hit")

    fake_module.WebSearcher = lambda: _FakeSearcher()

    plugin = _import_plugin_module()
    raw = plugin._tool_web_search(query="brent")
    payload = json.loads(raw)
    assert "error" in payload
    assert "rate limit" in payload["error"]


def test_web_search_truncates_long_snippet(fake_searcher_module) -> None:
    fake_module, _ = fake_searcher_module
    long_snippet = "x" * 1000

    class _FakeHit:
        def __init__(self):
            self.title = "t"
            self.url = "u"
            self.hostname = "reuters.com"
            self.tier = "tier1"
            self.snippet = long_snippet
            self.age = None
            self.published = None

    class _FakeSearcher:
        @property
        def has_key(self):
            return True

        def search(self, *a, **kw):
            return [_FakeHit()]

    fake_module.WebSearcher = lambda: _FakeSearcher()

    plugin = _import_plugin_module()
    raw = plugin._tool_web_search(query="x")
    payload = json.loads(raw)
    snippet = payload["results"][0]["snippet"]
    assert len(snippet) <= 501  # 500 + ellipsis
    assert snippet.endswith("…")
