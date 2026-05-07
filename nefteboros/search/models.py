"""Доменные dataclass'ы для web-поиска."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class SearchHit:
    """Один результат web-поиска.

    Минимально достаточный shape для LLM-цитирования
    `[Источник: <hostname>, web]`. raw — оригинальный объект провайдера
    для отладки/расширения; в tool-response не сериализуется.
    """

    title: str
    url: str
    hostname: str
    snippet: str
    tier: str
    age: Optional[str] = None
    published: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


__all__ = ["SearchHit"]
