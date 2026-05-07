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
            # Original tier1
            ("reuters.com", "tier1"),
            ("www.bloomberg.com", "tier1"),
            ("ft.com", "tier1"),
            ("argusmedia.com", "tier1"),
            ("rbc.ru", "tier1"),
            ("vedomosti.ru", "tier1"),
            ("opec.org", "tier1"),
            ("eia.gov", "tier1"),
            # New tier1 — regulators (sanctions + budget)
            ("state.gov", "tier1"),
            ("ofac.treasury.gov", "tier1"),
            ("minfin.gov.ru", "tier1"),
            # New tier1 — global news
            ("bbc.com", "tier1"),
            ("nytimes.com", "tier1"),
            # New tier1 — top think tanks
            ("brookings.edu", "tier1"),
            ("atlanticcouncil.org", "tier1"),
            ("ieefa.org", "tier1"),
            ("energyandcleanair.org", "tier1"),
            # New tier1 — specialised energy
            ("lngjournal.com", "tier1"),
            # Original tier2
            ("cnbc.com", "tier2"),
            ("forbes.com", "tier2"),
            ("rg.ru", "tier2"),
            ("expert.ru", "tier2"),
            # New tier2 — global news/business
            ("aljazeera.com", "tier2"),
            ("politico.com", "tier2"),
            ("fortune.com", "tier2"),
            ("newsweek.com", "tier2"),
            ("foxnews.com", "tier2"),
            ("arabnews.com", "tier2"),
            ("asiatimes.com", "tier2"),
            ("finance.yahoo.com", "tier2"),
            # New tier2 — RU independent
            ("meduza.io", "tier2"),
            ("themoscowtimes.com", "tier2"),
            ("svoboda.org", "tier2"),
            ("forbes.ru", "tier2"),
            # New tier2 — RU industry/business
            ("portnews.ru", "tier2"),
            ("abnews.ru", "tier2"),
            ("business-gazeta.ru", "tier2"),
            # New tier2 — UA business
            ("forbes.ua", "tier2"),
            ("liga.net", "tier2"),
            ("epravda.com.ua", "tier2"),
            ("minfin.com.ua", "tier2"),
            # Original blacklist
            ("reddit.com", "blacklist"),
            ("dzen.ru", "blacklist"),
            ("vk.com", "blacklist"),
            ("zen.yandex.ru", "blacklist"),
            ("wikipedia.org", "blacklist"),
            ("mail.ru", "blacklist"),
            # New blacklist — yellow press / promo / aggregators
            ("life.ru", "blacklist"),
            ("moneytimes.ru", "blacklist"),
            ("investmint.ru", "blacklist"),
            ("seala.ru", "blacklist"),
            ("heygotrade.com", "blacklist"),
            ("litefinance.org", "blacklist"),
            ("discoveryalert.com.au", "blacklist"),
            ("globalmarketnews.com", "blacklist"),
            ("theglobalstatistics.com", "blacklist"),
            # Other — fallback for unknown
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
            # New tier1 subdomains
            ("travel.state.gov", "tier1"),
            ("www.brookings.edu", "tier1"),
            ("ru.atlanticcouncil.org", "tier1"),
            # tier2 subdomains — главный кейс из live smoke
            ("markets.businessinsider.com", "tier2"),
            ("www.cnbc.com", "tier2"),
            ("oil.expert.ru", "tier2"),
            # New tier2 subdomains
            ("ru.themoscowtimes.com", "tier2"),
            ("m.business-gazeta.ru", "tier2"),
            ("biz.liga.net", "tier2"),
            ("index.minfin.com.ua", "tier2"),
            # blacklist subdomains
            ("ru.wikipedia.org", "blacklist"),
            ("en.wikipedia.org", "blacklist"),
            # finance.mail.ru покрыт TestTierFirstOverride (whitelist в TIER2)
            ("news.mail.ru", "blacklist"),
            ("old.reddit.com", "blacklist"),
            ("api.dzen.ru", "blacklist"),
            # New blacklist subdomains
            ("news.life.ru", "blacklist"),
            ("blog.litefinance.org", "blacklist"),
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
        assert is_blacklisted("OLD.REDDIT.COM")
        assert is_blacklisted("news.mail.ru")  # generic news aggregator

    def test_empty_host_not_blacklisted(self) -> None:
        assert not is_blacklisted("")
        assert not is_blacklisted("   ")


class TestTierFirstOverride:
    """Tier membership имеет приоритет над blacklist subdomain match —
    whitelist полезных subdomain'ов под blacklisted-корнями.

    Эмпирическая проверка показала: `finance.mail.ru` — финансовая
    редакция (интервью аналитиков, прогнозы), а не агрегатор. Корень
    `mail.ru` остаётся blacklist; `news.mail.ru` (general feed без
    финансовой специализации) — тоже blacklist через subdomain match.
    """

    def test_finance_mail_ru_whitelisted_to_tier2(self) -> None:
        assert classify("finance.mail.ru") == "tier2"
        assert not is_blacklisted("finance.mail.ru")

    def test_mail_ru_root_still_blacklist(self) -> None:
        assert classify("mail.ru") == "blacklist"
        assert is_blacklisted("mail.ru")

    def test_news_mail_ru_still_blacklist_via_subdomain(self) -> None:
        """`news.mail.ru` НЕ в TIER2 → idёт в blacklist через `mail.ru`."""
        assert classify("news.mail.ru") == "blacklist"
        assert is_blacklisted("news.mail.ru")

    def test_other_mail_ru_subdomains_still_blacklist(self) -> None:
        assert classify("pulse.mail.ru") == "blacklist"
        assert classify("foo.mail.ru") == "blacklist"

    def test_finance_subdomain_of_mail_inherits_tier2(self) -> None:
        """Если `api.finance.mail.ru` появится — он tier2 через subdomain
        match с `finance.mail.ru` (которое явно в TIER2)."""
        assert classify("api.finance.mail.ru") == "tier2"
