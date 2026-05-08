"""Anti-hallucination citation validator.

Цитаты в ответе агента (``[Source title, p.X]`` для RAG, markdown-link для
web, ``[Forecast: model, CI N%]`` для forecast) проверяются на соответствие
реально извлечённым chunks / web hits / forecast tool-calls.

Public API:

- :func:`validate` — основная функция валидации.
- :class:`CitationReport` — структурированный отчёт.
- ``parse_*_citations`` — низкоуровневые парсеры по форматам (для eval).
- ``RAG_PATTERN`` / ``WEB_PATTERN`` / ``FORECAST_PATTERN`` — regex'ы.

История:

- v2.0.0: модуль был заглушкой (см. README.md:18 «Anti-hallucination
  валидатор для RAG»). Реализация — этот файл, см. roadmap-v2.1 D6.
- v2.1.0 (planned): ``rewrite.py`` — переписывание ответа при провале
  валидации (см. D4 open question).
"""
from nefteboros.citations.patterns import (
    FORECAST_PATTERN,
    RAG_PATTERN,
    WEB_PATTERN,
    ParsedForecastCitation,
    ParsedRagCitation,
    ParsedWebCitation,
    parse_forecast_citations,
    parse_rag_citations,
    parse_web_citations,
)
from nefteboros.citations.validator import CitationReport, validate

__all__ = [
    # Validation API
    "validate",
    "CitationReport",
    # Patterns
    "RAG_PATTERN",
    "WEB_PATTERN",
    "FORECAST_PATTERN",
    # Parsed dataclasses
    "ParsedRagCitation",
    "ParsedWebCitation",
    "ParsedForecastCitation",
    # Parsers
    "parse_rag_citations",
    "parse_web_citations",
    "parse_forecast_citations",
]
