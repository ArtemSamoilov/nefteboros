"""Hostname-based tier filter для web-search (ТЗ §2.3).

TIER1 — verified business/industry sources с редакторской ответственностью
        (Reuters/Bloomberg/FT/Argus/S&P Platts/Wood Mackenzie + RU-tier1
        деловые: РБК/Ведомости/Коммерсант/Интерфакс/ТАСС + регуляторы
        OPEC/IEA/EIA).
TIER2 — общие деловые/энергетические СМИ.
BLACKLIST — агрегаторы без оригинального контента, форумы, соцсети,
            yellow-press источники. Всегда отбрасываются.
Остальные → "other": не блокируется, но помечается, чтобы LLM видел
            «не верифицирован».

Списки настраиваются через ENV (полное переопределение, не дополнение):
- NEFTEBOROS_WEB_TIER1_HOSTS=reuters.com,bloomberg.com,...
- NEFTEBOROS_WEB_TIER2_HOSTS=...
- NEFTEBOROS_WEB_BLACKLIST_HOSTS=...
"""
from __future__ import annotations

import os

_DEFAULT_TIER1 = frozenset({
    # EN business/industry
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "argusmedia.com",
    "spglobal.com",
    "platts.com",
    "woodmac.com",
    "energyintel.com",
    "rystadenergy.com",
    # Regulators / institutional
    "iea.org",
    "opec.org",
    "eia.gov",
    # RU business
    "rbc.ru",
    "vedomosti.ru",
    "kommersant.ru",
    "interfax.ru",
    "tass.ru",
    "tass.com",
    # Specialised energy press
    "oilprice.com",
    "energyintelligence.com",
})

_DEFAULT_TIER2 = frozenset({
    # EN general business
    "cnbc.com",
    "marketwatch.com",
    "forbes.com",
    "businessinsider.com",
    "axios.com",
    # RU general/energy
    "rg.ru",
    "iz.ru",
    "expert.ru",
    "neftegaz.ru",
    "tek-all.ru",
    "energyland.info",
    "lenta.ru",
})

_DEFAULT_BLACKLIST = frozenset({
    "reddit.com",
    "quora.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "vk.com",
    "ok.ru",
    "zen.yandex.ru",
    "dzen.ru",
    "pikabu.ru",
    "livejournal.com",
    "medium.com",
})


def _from_env(var: str, default: frozenset[str]) -> frozenset[str]:
    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def get_tier1() -> frozenset[str]:
    return _from_env("NEFTEBOROS_WEB_TIER1_HOSTS", _DEFAULT_TIER1)


def get_tier2() -> frozenset[str]:
    return _from_env("NEFTEBOROS_WEB_TIER2_HOSTS", _DEFAULT_TIER2)


def get_blacklist() -> frozenset[str]:
    return _from_env("NEFTEBOROS_WEB_BLACKLIST_HOSTS", _DEFAULT_BLACKLIST)


def normalize_hostname(host: str) -> str:
    h = (host or "").lower().strip()
    if h.startswith("www."):
        h = h[4:]
    return h


def classify(host: str) -> str:
    """Returns 'tier1' | 'tier2' | 'blacklist' | 'other'."""
    h = normalize_hostname(host)
    if h in get_blacklist():
        return "blacklist"
    if h in get_tier1():
        return "tier1"
    if h in get_tier2():
        return "tier2"
    return "other"


def is_blacklisted(host: str) -> bool:
    return normalize_hostname(host) in get_blacklist()


__all__ = [
    "classify",
    "is_blacklisted",
    "normalize_hostname",
    "get_tier1",
    "get_tier2",
    "get_blacklist",
]
