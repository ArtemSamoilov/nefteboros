"""Public API доменного web-search.

`WebSearcher.search(query, ...) -> list[SearchHit]` — главный entry point.
Применяет blacklist (всегда), tier_filter (по выбору), и кэширует
сырой ответ Brave на 1 час (in-memory TTL). Lang detection — внутри.
"""
from __future__ import annotations

import logging
import os

from nefteboros.search.brave import (
    BraveAuthError,
    BraveClient,
    BraveError,
    BraveRateLimitError,
)
from nefteboros.search.cache import TTLCache
from nefteboros.search.lang import detect_lang
from nefteboros.search.models import SearchHit
from nefteboros.search.tiers import is_blacklisted

logger = logging.getLogger(__name__)


_DEFAULT_K = 5
_MAX_K = 10
_DEFAULT_FRESHNESS = "pw"
_VALID_FRESHNESS = frozenset({"pd", "pw", "pm", "py"})
_VALID_TIER_FILTER = frozenset({"all", "tier1"})

# Запрашиваем у Brave с запасом, чтобы blacklist+tier filter не
# просаживал итоговое k. Множитель — 2× от k (но не менее 10).
_OVER_FETCH_MULT = 2
_MIN_BRAVE_COUNT = 10

# Module-level кэш — общий между tool_handler инстансами WebSearcher.
_CACHE = TTLCache(ttl_seconds=3600, max_size=256)


class WebSearcher:
    def __init__(self, client: BraveClient | None = None) -> None:
        self._client = client or BraveClient()

    @property
    def has_key(self) -> bool:
        return self._client.has_key

    def search(
        self,
        query: str,
        k: int = _DEFAULT_K,
        freshness: str = _DEFAULT_FRESHNESS,
        tier_filter: str = "all",
    ) -> list[SearchHit]:
        q = (query or "").strip()
        if not q:
            return []

        if freshness not in _VALID_FRESHNESS:
            env_freshness = os.environ.get("BRAVE_FRESHNESS", _DEFAULT_FRESHNESS)
            freshness = env_freshness if env_freshness in _VALID_FRESHNESS else _DEFAULT_FRESHNESS
        if tier_filter not in _VALID_TIER_FILTER:
            tier_filter = "all"

        try:
            k_int = max(1, min(_MAX_K, int(k)))
        except (TypeError, ValueError):
            k_int = _DEFAULT_K

        lang = detect_lang(q)
        cache_key = (q, freshness, lang)

        cached = _CACHE.get(cache_key)
        if cached is None:
            count = max(_MIN_BRAVE_COUNT, k_int * _OVER_FETCH_MULT)
            cached = self._client.search(
                q, count=count, freshness=freshness, lang=lang
            )
            _CACHE.set(cache_key, cached)

        result = [h for h in cached if not is_blacklisted(h.hostname)]
        if tier_filter == "tier1":
            result = [h for h in result if h.tier == "tier1"]
        return result[:k_int]


__all__ = [
    "WebSearcher",
    "SearchHit",
    "BraveClient",
    "BraveError",
    "BraveAuthError",
    "BraveRateLimitError",
]
