"""Tests для nefteboros.search.tiers — host classifier и ENV-override."""
from __future__ import annotations

import pytest

from nefteboros.search.tiers import (
    classify,
    get_blacklist,
    get_tier1,
    get_tier2,
    is_blacklisted,
    normalize_hostname,
)


class TestNormalizeHostname:
    @pytest.mark.parametrize(
        "given, expected",
        [
            ("REUTERS.com", "reuters.com"),
            ("www.bloomberg.com", "bloomberg.com"),
            ("WWW.RBC.RU", "rbc.ru"),
            ("  vedomosti.ru  ", "vedomosti.ru"),
            ("", ""),
        ],
    )
    def test_normalizes(self, given: str, expected: str) -> None:
        assert normalize_hostname(given) == expected


class TestClassifyDefaults:
    @pytest.mark.parametrize(
        "host, expected_tier",
        [
            ("reuters.com", "tier1"),
            ("www.bloomberg.com", "tier1"),
            ("ft.com", "tier1"),
            ("argusmedia.com", "tier1"),
            ("rbc.ru", "tier1"),
            ("vedomosti.ru", "tier1"),
            ("opec.org", "tier1"),
            ("eia.gov", "tier1"),
            ("cnbc.com", "tier2"),
            ("forbes.com", "tier2"),
            ("rg.ru", "tier2"),
            ("expert.ru", "tier2"),
            ("reddit.com", "blacklist"),
            ("dzen.ru", "blacklist"),
            ("vk.com", "blacklist"),
            ("zen.yandex.ru", "blacklist"),
            ("wikipedia.org", "blacklist"),
            ("mail.ru", "blacklist"),
            ("unknown-blog.example", "other"),
        ],
    )
    def test_classifies_known_hosts(self, host: str, expected_tier: str) -> None:
        assert classify(host) == expected_tier


class TestSubdomainMatch:
    """Subdomain-aware classification: base `bloomberg.com` ловит
    `markets.bloomberg.com`, но не `notbloomberg.com`."""

    @pytest.mark.parametrize(
        "host, expected_tier",
        [
            # tier1 subdomains
            ("markets.bloomberg.com", "tier1"),
            ("news.bloomberg.com", "tier1"),
            ("uk.reuters.com", "tier1"),
            ("www.ft.com", "tier1"),
            ("api.opec.org", "tier1"),
            # tier2 subdomains — главный кейс из live smoke
            ("markets.businessinsider.com", "tier2"),
            ("www.cnbc.com", "tier2"),
            ("oil.expert.ru", "tier2"),
            # blacklist subdomains
            ("ru.wikipedia.org", "blacklist"),
            ("en.wikipedia.org", "blacklist"),
            ("finance.mail.ru", "blacklist"),
            ("news.mail.ru", "blacklist"),
            ("old.reddit.com", "blacklist"),
            ("api.dzen.ru", "blacklist"),
        ],
    )
    def test_subdomain_inherits_tier(self, host: str, expected_tier: str) -> None:
        assert classify(host) == expected_tier

    @pytest.mark.parametrize(
        "false_suffix",
        [
            "notbloomberg.com",
            "fakereuters.com",
            "notwikipedia.org",
            "evilmail.ru",
            "ftnews.com",  # не должно матчить ft.com
        ],
    )
    def test_false_suffix_not_matched(self, false_suffix: str) -> None:
        """Без точки в шаблоне `evilmail.ru` ложно матчился бы на
        `mail.ru`. Проверяем, что endswith идёт ровно по '.' + entry."""
        assert classify(false_suffix) == "other"


class TestEnvOverride:
    def test_tier1_env_replaces_default(self, monkeypatch) -> None:
        monkeypatch.setenv(
            "NEFTEBOROS_WEB_TIER1_HOSTS",
            "custom1.com, Custom2.RU, custom3.com",
        )
        tier1 = get_tier1()
        assert tier1 == frozenset({"custom1.com", "custom2.ru", "custom3.com"})
        # Reuters больше не tier1 (override полностью замещает default)
        assert classify("reuters.com") != "tier1"
        assert classify("custom1.com") == "tier1"

    def test_tier2_env_replaces_default(self, monkeypatch) -> None:
        monkeypatch.setenv("NEFTEBOROS_WEB_TIER2_HOSTS", "axx.com")
        assert get_tier2() == frozenset({"axx.com"})

    def test_blacklist_env_replaces_default(self, monkeypatch) -> None:
        monkeypatch.setenv("NEFTEBOROS_WEB_BLACKLIST_HOSTS", "bad.com,evil.org")
        assert get_blacklist() == frozenset({"bad.com", "evil.org"})
        assert is_blacklisted("bad.com")
        # Subdomain match работает и для ENV-override
        assert is_blacklisted("foo.bad.com")
        # Дефолтный reddit больше НЕ blacklisted после override
        assert not is_blacklisted("reddit.com")

    def test_empty_env_keeps_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("NEFTEBOROS_WEB_TIER1_HOSTS", "")
        assert "reuters.com" in get_tier1()


class TestIsBlacklisted:
    def test_normalises_before_check(self) -> None:
        assert is_blacklisted("WWW.REDDIT.COM")
        assert is_blacklisted("www.dzen.ru")
        assert not is_blacklisted("reuters.com")

    def test_subdomain_blacklisted(self) -> None:
        assert is_blacklisted("ru.wikipedia.org")
        assert is_blacklisted("finance.mail.ru")
        assert is_blacklisted("OLD.REDDIT.COM")

    def test_empty_host_not_blacklisted(self) -> None:
        assert not is_blacklisted("")
        assert not is_blacklisted("   ")
