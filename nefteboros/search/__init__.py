"""Веб-поиск с фильтрацией источников по уровню доверия.

Будет содержать:
  - brave.py     — клиент Brave Search API
  - filters.py   — tier-1 whitelist / tier-2 / blacklist по hostname
  - schema.py    — WebResult с полями source_tier, hostname, snippet, url
  - tools.py     — обёртка для использования из LangGraph

См. docs/adr/0006-brave-and-filtering.md (TBD).
"""
