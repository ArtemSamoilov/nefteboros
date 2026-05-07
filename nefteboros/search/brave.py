"""Brave Search API клиент.

API docs: https://api.search.brave.com/app/documentation/web-search/get-started

Endpoint:  GET https://api.search.brave.com/res/v1/web/search
Header:    X-Subscription-Token: <BRAVE_API_KEY>
Params:    q, count(1..20), freshness(pd|pw|pm|py),
           search_lang, country, ui_lang.

Free tier: 1 RPS, 2000 queries/month. На превышение — 429 с
небольшим backoff (1.5 сек) + 1 retry, дальше отдаём BraveRateLimitError.

Sync клиент: Ouroboros tool-handler сам sync (см. _tool_rag_search/
_tool_analyst_query); async-обёртка не нужна.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from nefteboros.search.lang import brave_params_for_lang, detect_lang
from nefteboros.search.models import SearchHit
from nefteboros.search.tiers import classify, normalize_hostname

logger = logging.getLogger(__name__)


_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_DEFAULT_TIMEOUT = 10.0
_MAX_COUNT = 20  # Brave hard limit per request


class BraveError(RuntimeError):
    """Базовое исключение для всех проблем Brave-клиента."""


class BraveAuthError(BraveError):
    """Ключ отсутствует / неверный / 401/403."""


class BraveRateLimitError(BraveError):
    """429 после retry."""


class BraveClient:
    """Sync httpx-based Brave Search клиент.

    Read BRAVE_API_KEY на init. Если ключа нет — search() поднимает
    BraveAuthError на первом вызове, не падая на конструкторе (lazy
    pattern, как в других модулях skill'а).
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        endpoint: str = _BRAVE_ENDPOINT,
    ) -> None:
        env_key = os.environ.get("BRAVE_API_KEY", "")
        self._api_key = (api_key if api_key is not None else env_key).strip()
        self._timeout = timeout
        self._endpoint = endpoint

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    def search(
        self,
        query: str,
        count: int = 10,
        freshness: str = "pw",
        lang: str | None = None,
    ) -> list[SearchHit]:
        if not self._api_key:
            raise BraveAuthError(
                "BRAVE_API_KEY not set. Get key at brave.com/search/api/."
            )

        q = (query or "").strip()
        if not q:
            return []

        count = max(1, min(_MAX_COUNT, int(count)))
        if lang is None:
            lang = detect_lang(q)

        params = {
            "q": q,
            "count": count,
            "freshness": freshness,
            **brave_params_for_lang(lang),
        }
        headers = {
            "X-Subscription-Token": self._api_key,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(
                        self._endpoint, headers=headers, params=params
                    )
                status = response.status_code
                if status in (401, 403):
                    raise BraveAuthError(
                        f"Brave auth failed: HTTP {status}"
                    )
                if status == 429:
                    if attempt == 0:
                        time.sleep(1.5)
                        continue
                    raise BraveRateLimitError("Brave rate limit (429) after retry")
                if status >= 500:
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    raise BraveError(f"Brave 5xx: HTTP {status}")
                if status >= 400:
                    raise BraveError(
                        f"Brave bad request: HTTP {status} {response.text[:200]}"
                    )
                return _parse_results(response.json())
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise BraveError(f"Brave network error: {exc!r}") from exc

        raise BraveError(f"Brave failed after retry: {last_exc!r}")


def _parse_results(payload: dict[str, Any]) -> list[SearchHit]:
    """Brave web/search response → list[SearchHit].

    Brave shape: payload["web"]["results"] = [
        {title, url, description, age, page_age,
         meta_url: {hostname, ...}, profile: {...}}
    ].
    """
    hits: list[SearchHit] = []
    web = (payload or {}).get("web") or {}
    results = web.get("results") or []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or ""
        meta = item.get("meta_url") or {}
        hostname = meta.get("hostname") or ""
        if not hostname and url:
            try:
                hostname = urlparse(url).hostname or ""
            except Exception:
                hostname = ""
        hostname = normalize_hostname(hostname)
        hits.append(
            SearchHit(
                title=str(item.get("title") or ""),
                url=str(url),
                hostname=hostname,
                snippet=str(item.get("description") or ""),
                tier=classify(hostname),
                age=item.get("age"),
                published=item.get("page_age"),
                raw=item,
            )
        )
    return hits


__all__ = [
    "BraveClient",
    "BraveError",
    "BraveAuthError",
    "BraveRateLimitError",
]
