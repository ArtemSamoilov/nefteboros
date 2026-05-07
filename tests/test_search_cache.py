"""Tests для nefteboros.search.cache — TTL поведение."""
from __future__ import annotations

import pytest

from nefteboros.search.cache import TTLCache


class TestTTLCache:
    def test_get_missing_returns_none(self) -> None:
        cache = TTLCache(ttl_seconds=60)
        assert cache.get("missing") is None

    def test_set_then_get_returns_value(self) -> None:
        cache = TTLCache(ttl_seconds=60)
        cache.set("k", [1, 2, 3])
        assert cache.get("k") == [1, 2, 3]

    def test_expired_returns_none(self, monkeypatch) -> None:
        """После истечения TTL get() возвращает None и удаляет запись."""
        # Контролируем монотонное время внутри cache.
        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        cache = TTLCache(ttl_seconds=10)
        monkeypatch.setattr("nefteboros.search.cache.time.monotonic", fake_monotonic)

        cache.set("k", "value")
        assert cache.get("k") == "value"

        fake_now[0] = 11.0
        assert cache.get("k") is None
        assert len(cache) == 0  # запись удалена

    def test_evicts_when_full(self) -> None:
        cache = TTLCache(ttl_seconds=60, max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        assert len(cache) == 3

        cache.set("d", 4)  # триггерит eviction
        assert len(cache) == 3
        # 'd' точно есть, какая-то из старых выкинута
        assert cache.get("d") == 4

    def test_clear_empties_cache(self) -> None:
        cache = TTLCache()
        cache.set("k1", 1)
        cache.set("k2", 2)
        cache.clear()
        assert len(cache) == 0
        assert cache.get("k1") is None

    def test_tuple_key_works(self) -> None:
        """Кэш используется с tuple-ключом (query, freshness, lang)."""
        cache = TTLCache()
        cache.set(("brent price", "pw", "en"), ["hit1", "hit2"])
        assert cache.get(("brent price", "pw", "en")) == ["hit1", "hit2"]
        assert cache.get(("brent price", "pd", "en")) is None
