"""Tests для nefteboros.search.WebSearcher — фасад."""
from __future__ import annotations

import pytest

from nefteboros.search import WebSearcher
from nefteboros.search.brave import BraveAuthError, BraveError
from nefteboros.search.models import SearchHit


def _hit(host: str, tier: str = "tier1", title: str = "x") -> SearchHit:
    return SearchHit(
        title=title,
        url=f"https://{host}/article",
        hostname=host,
        snippet="snippet",
        tier=tier,
    )


class _FakeBraveClient:
    """Управляемый стэб BraveClient — записывает аргументы, отдаёт preset."""

    def __init__(self, hits=None, error=None, has_key=True):
        self._hits = hits or []
        self._error = error
        self.has_key = has_key
        self.calls: list[dict] = []

    def search(self, query, *, count=10, freshness="pw", lang=None):
        self.calls.append(
            {"query": query, "count": count, "freshness": freshness, "lang": lang}
        )
        if self._error is not None:
            raise self._error
        return list(self._hits)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Каждый тест начинает с пустого кэша."""
    from nefteboros.search import _CACHE

    _CACHE.clear()
    yield
    _CACHE.clear()


class TestEmptyQuery:
    def test_empty_returns_empty(self) -> None:
        searcher = WebSearcher(client=_FakeBraveClient())
        assert searcher.search("") == []
        assert searcher.search("   ") == []


class TestBlacklistFilter:
    def test_blacklisted_hosts_filtered_out(self) -> None:
        client = _FakeBraveClient(
            hits=[
                _hit("reuters.com", "tier1"),
                _hit("dzen.ru", "blacklist"),
                _hit("vk.com", "blacklist"),
                _hit("bloomberg.com", "tier1"),
            ]
        )
        searcher = WebSearcher(client=client)
        hits = searcher.search("test", k=10)
        hostnames = [h.hostname for h in hits]
        assert "reuters.com" in hostnames
        assert "bloomberg.com" in hostnames
        assert "dzen.ru" not in hostnames
        assert "vk.com" not in hostnames


class TestTierFilter:
    def test_tier1_only_keeps_tier1(self) -> None:
        client = _FakeBraveClient(
            hits=[
                _hit("reuters.com", "tier1"),
                _hit("forbes.com", "tier2"),
                _hit("unknown.example", "other"),
            ]
        )
        searcher = WebSearcher(client=client)
        hits = searcher.search("x", k=10, tier_filter="tier1")
        assert [h.hostname for h in hits] == ["reuters.com"]

    def test_all_tier_keeps_tier1_tier2_other(self) -> None:
        client = _FakeBraveClient(
            hits=[
                _hit("reuters.com", "tier1"),
                _hit("forbes.com", "tier2"),
                _hit("unknown.example", "other"),
            ]
        )
        searcher = WebSearcher(client=client)
        hits = searcher.search("x", k=10, tier_filter="all")
        assert len(hits) == 3


class TestKLimit:
    def test_returns_at_most_k(self) -> None:
        client = _FakeBraveClient(
            hits=[_hit(f"h{i}.com", "tier1") for i in range(10)]
        )
        searcher = WebSearcher(client=client)
        hits = searcher.search("x", k=3)
        assert len(hits) == 3

    def test_clamps_k_to_max(self) -> None:
        client = _FakeBraveClient(
            hits=[_hit(f"h{i}.com", "tier1") for i in range(15)]
        )
        searcher = WebSearcher(client=client)
        hits = searcher.search("x", k=999)
        assert len(hits) == 10  # _MAX_K

    def test_overfetches_to_compensate_filter(self) -> None:
        """Brave запрашивается с count >= k*2, чтобы blacklist/tier filter
        не оставил <k результатов после фильтрации."""
        client = _FakeBraveClient(hits=[])
        searcher = WebSearcher(client=client)
        searcher.search("x", k=5)
        assert client.calls[0]["count"] >= 10


class TestCache:
    def test_second_call_reuses_cache(self) -> None:
        client = _FakeBraveClient(hits=[_hit("reuters.com", "tier1")])
        searcher = WebSearcher(client=client)

        searcher.search("brent oil", k=5)
        searcher.search("brent oil", k=5)
        assert len(client.calls) == 1  # второй раз — из кэша

    def test_different_freshness_different_cache(self) -> None:
        client = _FakeBraveClient(hits=[])
        searcher = WebSearcher(client=client)

        searcher.search("brent", k=5, freshness="pw")
        searcher.search("brent", k=5, freshness="pd")
        assert len(client.calls) == 2

    def test_different_lang_different_cache(self) -> None:
        """RU и EN запросы — разные cache_key."""
        client = _FakeBraveClient(hits=[])
        searcher = WebSearcher(client=client)

        searcher.search("brent crude", k=5)
        searcher.search("нефть Brent", k=5)
        assert len(client.calls) == 2


class TestErrorPropagation:
    def test_brave_error_propagates(self) -> None:
        client = _FakeBraveClient(error=BraveError("boom"))
        searcher = WebSearcher(client=client)
        with pytest.raises(BraveError):
            searcher.search("x")

    def test_auth_error_propagates(self) -> None:
        client = _FakeBraveClient(error=BraveAuthError("no key"))
        searcher = WebSearcher(client=client)
        with pytest.raises(BraveAuthError):
            searcher.search("x")


class TestFreshnessValidation:
    def test_invalid_freshness_falls_back(self) -> None:
        client = _FakeBraveClient(hits=[])
        searcher = WebSearcher(client=client)
        searcher.search("brent", freshness="garbage")
        # должно не упасть и вызвать Brave с дефолтом pw (или ENV-override)
        assert client.calls[0]["freshness"] in {"pd", "pw", "pm", "py"}

    def test_invalid_tier_falls_back_to_all(self) -> None:
        client = _FakeBraveClient(
            hits=[_hit("reuters.com", "tier1"), _hit("unknown.com", "other")]
        )
        searcher = WebSearcher(client=client)
        hits = searcher.search("brent", tier_filter="garbage")
        assert len(hits) == 2  # 'all' пропускает оба
