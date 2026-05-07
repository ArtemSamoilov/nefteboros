"""Tests для nefteboros.search.brave — Brave API клиент.

httpx мок через monkeypatch (паттерн test_search_tool.py — без respx).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nefteboros.search.brave import (
    BraveAuthError,
    BraveClient,
    BraveError,
    BraveRateLimitError,
)


# =============================================================================
# Helpers — fake httpx Client
# =============================================================================


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """httpx.Client стэб с программируемыми ответами."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, *, headers=None, params=None):
        self.calls.append({"url": url, "headers": headers or {}, "params": params or {}})
        if not self._responses:
            raise RuntimeError("FakeClient: no more responses programmed")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _patch_httpx(monkeypatch, fake_client: _FakeClient) -> None:
    monkeypatch.setattr(
        "nefteboros.search.brave.httpx.Client",
        lambda *a, **kw: fake_client,
    )


# =============================================================================
# Tests
# =============================================================================


class TestAuth:
    def test_no_key_raises_on_search(self, monkeypatch) -> None:
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        client = BraveClient()
        assert client.has_key is False
        with pytest.raises(BraveAuthError):
            client.search("brent")

    def test_explicit_key_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv("BRAVE_API_KEY", "env-key")
        client = BraveClient(api_key="explicit-key")
        assert client.has_key is True

    def test_401_raises_auth_error(self, monkeypatch) -> None:
        client = BraveClient(api_key="bad-key")
        fake = _FakeClient([_FakeResponse(401, {"error": "unauthorized"})])
        _patch_httpx(monkeypatch, fake)

        with pytest.raises(BraveAuthError):
            client.search("brent")


class TestHappyPath:
    def test_parses_brave_response(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        payload = {
            "web": {
                "results": [
                    {
                        "title": "OPEC keeps quotas",
                        "url": "https://www.reuters.com/article/opec",
                        "description": "OPEC+ extended cuts...",
                        "age": "2 hours ago",
                        "page_age": "2026-05-07T08:00:00Z",
                        "meta_url": {"hostname": "www.reuters.com"},
                    },
                    {
                        "title": "Brent climbs",
                        "url": "https://oilprice.com/news/123",
                        "description": "Brent crude crossed $90...",
                        "meta_url": {"hostname": "oilprice.com"},
                    },
                    {
                        "title": "Yandex zen post",
                        "url": "https://dzen.ru/some-post",
                        "description": "...",
                        "meta_url": {"hostname": "dzen.ru"},
                    },
                ]
            }
        }
        fake = _FakeClient([_FakeResponse(200, payload)])
        _patch_httpx(monkeypatch, fake)

        hits = client.search("OPEC quotas latest", count=10, freshness="pw")
        assert len(hits) == 3

        h0 = hits[0]
        assert h0.title == "OPEC keeps quotas"
        assert h0.url == "https://www.reuters.com/article/opec"
        assert h0.hostname == "reuters.com"
        assert h0.tier == "tier1"
        assert h0.age == "2 hours ago"
        assert h0.published == "2026-05-07T08:00:00Z"

        # blacklist хост помечен tier='blacklist' (отсев — на уровне WebSearcher,
        # не клиента)
        h_dzen = next(h for h in hits if h.hostname == "dzen.ru")
        assert h_dzen.tier == "blacklist"

    def test_passes_lang_params_for_ru_query(self, monkeypatch) -> None:
        """RU-запрос → search_lang=ru, country=RU."""
        client = BraveClient(api_key="k")
        fake = _FakeClient([_FakeResponse(200, {"web": {"results": []}})])
        _patch_httpx(monkeypatch, fake)

        client.search("Что говорит OPEC")
        assert len(fake.calls) == 1
        params = fake.calls[0]["params"]
        assert params["search_lang"] == "ru"
        assert params["country"] == "RU"
        assert params["ui_lang"] == "ru-RU"

    def test_passes_lang_params_for_en_query(self, monkeypatch) -> None:
        """EN-запрос → search_lang=en, country=US."""
        client = BraveClient(api_key="k")
        fake = _FakeClient([_FakeResponse(200, {"web": {"results": []}})])
        _patch_httpx(monkeypatch, fake)

        client.search("Brent oil price")
        params = fake.calls[0]["params"]
        assert params["search_lang"] == "en"
        assert params["country"] == "US"

    def test_explicit_lang_overrides_detection(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        fake = _FakeClient([_FakeResponse(200, {"web": {"results": []}})])
        _patch_httpx(monkeypatch, fake)

        client.search("Brent oil", lang="ru")
        assert fake.calls[0]["params"]["search_lang"] == "ru"

    def test_count_clamped_to_max_20(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        fake = _FakeClient([_FakeResponse(200, {"web": {"results": []}})])
        _patch_httpx(monkeypatch, fake)

        client.search("brent", count=999)
        assert fake.calls[0]["params"]["count"] == 20

    def test_subscription_token_header(self, monkeypatch) -> None:
        client = BraveClient(api_key="my-secret")
        fake = _FakeClient([_FakeResponse(200, {"web": {"results": []}})])
        _patch_httpx(monkeypatch, fake)

        client.search("x")
        assert fake.calls[0]["headers"]["X-Subscription-Token"] == "my-secret"


class TestRetries:
    def test_429_retries_once_then_raises(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        fake = _FakeClient([
            _FakeResponse(429, {}, "rate limited"),
            _FakeResponse(429, {}, "rate limited again"),
        ])
        _patch_httpx(monkeypatch, fake)
        # Срезаем sleep, чтобы тест не залипал на retry-backoff.
        monkeypatch.setattr("nefteboros.search.brave.time.sleep", lambda *_: None)

        with pytest.raises(BraveRateLimitError):
            client.search("brent")
        assert len(fake.calls) == 2

    def test_429_recovers_on_retry(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        fake = _FakeClient([
            _FakeResponse(429),
            _FakeResponse(200, {"web": {"results": []}}),
        ])
        _patch_httpx(monkeypatch, fake)
        monkeypatch.setattr("nefteboros.search.brave.time.sleep", lambda *_: None)

        hits = client.search("brent")
        assert hits == []
        assert len(fake.calls) == 2

    def test_5xx_retries_once_then_raises(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        fake = _FakeClient([
            _FakeResponse(503),
            _FakeResponse(503),
        ])
        _patch_httpx(monkeypatch, fake)
        monkeypatch.setattr("nefteboros.search.brave.time.sleep", lambda *_: None)

        with pytest.raises(BraveError):
            client.search("brent")

    def test_network_error_retries_once(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        fake = _FakeClient([
            httpx.ConnectError("conn refused"),
            _FakeResponse(200, {"web": {"results": []}}),
        ])
        _patch_httpx(monkeypatch, fake)
        monkeypatch.setattr("nefteboros.search.brave.time.sleep", lambda *_: None)

        hits = client.search("brent")
        assert hits == []


class TestEmptyOrInvalid:
    def test_empty_query_returns_empty(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        # FakeClient не должен дёргаться на пустом query
        fake = _FakeClient([])
        _patch_httpx(monkeypatch, fake)

        assert client.search("") == []
        assert client.search("   ") == []
        assert len(fake.calls) == 0

    def test_400_raises(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        fake = _FakeClient([_FakeResponse(400, {}, "bad request")])
        _patch_httpx(monkeypatch, fake)

        with pytest.raises(BraveError):
            client.search("brent")

    def test_missing_meta_url_falls_back_to_url_parse(self, monkeypatch) -> None:
        client = BraveClient(api_key="k")
        payload = {
            "web": {
                "results": [
                    {
                        "title": "x",
                        "url": "https://www.bloomberg.com/article",
                        "description": "y",
                        # meta_url отсутствует — hostname извлекается из url
                    }
                ]
            }
        }
        fake = _FakeClient([_FakeResponse(200, payload)])
        _patch_httpx(monkeypatch, fake)

        hits = client.search("brent")
        assert len(hits) == 1
        assert hits[0].hostname == "bloomberg.com"
        assert hits[0].tier == "tier1"
