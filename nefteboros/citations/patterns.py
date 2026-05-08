"""Regex-паттерны для трёх форматов цитат v2.0.0.

Форматы определены в [prompts/SYSTEM.md:74](../../prompts/SYSTEM.md) и
[ADR-0019](../../docs/adr/0019-system-prompt-analyst.md):

- **RAG**:      ``[Source title, p.X]``         или   ``[Source title, p.X-Y]``
- **Web**:      ``[<title>](<url>) — <hostname>, web``  (markdown-link)
- **Forecast**: ``[Forecast: model, CI X%]``    или   ``[Forecast: model, CI 80/95%]``

Решения:

1. **RAG source greedy без `]`.** В корпусе встречаются source_title с
   запятой внутри (``CRS — U.S. Conflict with Iran (March 26, 2026)``,
   ``CRS — Iran Conflict and the Strait of Hormuz: Impacts on Oil, Gas, ...``).
   Non-greedy `[^,\\]]+?` срезал бы такие title до первой запятой. Greedy
   `[^\\]]+` с привязкой к терминирующему ``, p.\\d+]`` корректно ловит
   максимальный source.

2. **Page range optional.** Чанк может покрывать одну страницу (``p.14``)
   или диапазон (``p.5-10``) — обе формы валидны (см. SYSTEM.md:76).

3. **Forecast CI как digits-and-slash.** Текущий v2.0.0 формат пишется как
   ``CI 80%`` или ``CI 80/95%`` (двойной CI). Расширение под scenario
   (Track A, ADR-0023) — отдельным коммитом, см. roadmap-v2.1 D6.

4. **Web tier из ответа агента не извлекается.** Tier есть в SearchHit, но
   LLM пишет только title/url/hostname (см. SYSTEM.md:79). Validator
   сопоставит hit по url.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

# =============================================================================
# Regex-паттерны
# =============================================================================

#: RAG citation: ``[Source title, p.X]`` или ``[Source title, p.X-Y]``.
#: Greedy ``[^\]]+`` для покрытия source_title с запятыми внутри.
RAG_PATTERN: re.Pattern[str] = re.compile(
    r"\[(?P<source>[^\]]+?),\s*p\.(?P<page_start>\d+)(?:-(?P<page_end>\d+))?\]",
)

#: Web citation в markdown-формате: ``[title](url) — hostname, web``.
#: Зафиксирован в PR #33 (web-citation-markdown-links).
WEB_PATTERN: re.Pattern[str] = re.compile(
    r"\[(?P<title>[^\]]+)\]"
    r"\((?P<url>https?://[^\s)]+)\)"
    r"\s+—\s+(?P<host>[^\s,]+),\s*web",
)

#: Forecast citation. Два формата встречаются в v2.0.0:
#:
#: - **Spec из SYSTEM.md/ADR-0019**: ``[Forecast: model, CI X%]``
#: - **Реальный output `synthesize` ноды**: ``[forecast_model:asset@horizon, method, ADR-XXXX]``
#:   (см. ``nefteboros/graphs/state.py`` ``Citation.tag`` и
#:   ``nefteboros/graphs/nodes/synthesize.py`` ``_build_forecast_citations``).
#:
#: Это **расхождение спецификации и кода** в v2.0.0 — обнаружено при
#: первом real-baseline'е. Regex покрывает оба, чтобы baseline считался
#: корректно сейчас, без правки synthesize/SYSTEM.md (отдельный fix).
FORECAST_PATTERN: re.Pattern[str] = re.compile(
    r"\[(?:"
    r"Forecast:\s*(?P<model>[^,\]]+?),\s*CI\s*(?P<ci>[\d/]+%)"
    r"|"
    r"forecast_model:(?P<asset>[^@\]]+)@(?P<horizon>[^,\]]+)"
    r"(?:,\s*(?P<method>[^,\]]+))?(?:,\s*(?P<adr>ADR-\d+))?"
    r")\]",
)


# =============================================================================
# Distinguishability
# =============================================================================
# RAG_PATTERN из-за greedy подмаскировки может частично перекрываться с
# Forecast (`[Forecast: ARIMA, p.5]` гипотетический edge case — но
# `Forecast:` блокирует, потому что в RAG нет двоеточия после `[`). Web
# никогда не пересекается (из-за `](` следом). Поэтому порядок при парсинге:
#   1. parse_forecast_citations  (наиболее специфичный — префикс ``Forecast:``)
#   2. parse_web_citations       (markdown-форма с ``](...)``)
#   3. parse_rag_citations       (общий формат, после исключения предыдущих)
#
# В validator.py при сборке отчёта применяем именно такой порядок.


# =============================================================================
# Parsed citation dataclasses
# =============================================================================


@dataclass(frozen=True)
class ParsedRagCitation:
    """RAG citation, выделенная regex'ом из ответа агента."""

    source: str
    page_start: int
    page_end: int | None  # None если одностраничная цитата
    raw: str  # оригинальный matched substring для отчёта

    @property
    def is_range(self) -> bool:
        return self.page_end is not None


@dataclass(frozen=True)
class ParsedWebCitation:
    """Web citation в markdown-формате."""

    title: str
    url: str
    hostname: str
    raw: str


@dataclass(frozen=True)
class ParsedForecastCitation:
    """Forecast citation."""

    model: str
    ci: str  # ``80%`` или ``80/95%`` (как написано в ответе)
    raw: str


# =============================================================================
# Парсеры (yield все совпадения в порядке появления в тексте)
# =============================================================================


def parse_rag_citations(text: str) -> Iterator[ParsedRagCitation]:
    """Извлечь все RAG-цитаты из текста ответа агента."""
    for m in RAG_PATTERN.finditer(text):
        # Skip false-positives that are actually forecast/web matches:
        # Forecast префикс ``Forecast:`` означает что это не RAG.
        if m.group("source").lstrip().startswith("Forecast:"):
            continue
        page_end_raw = m.group("page_end")
        yield ParsedRagCitation(
            source=m.group("source").strip(),
            page_start=int(m.group("page_start")),
            page_end=int(page_end_raw) if page_end_raw else None,
            raw=m.group(0),
        )


def parse_web_citations(text: str) -> Iterator[ParsedWebCitation]:
    """Извлечь все web-цитаты в markdown-формате."""
    for m in WEB_PATTERN.finditer(text):
        yield ParsedWebCitation(
            title=m.group("title").strip(),
            url=m.group("url").strip(),
            hostname=m.group("host").strip(),
            raw=m.group(0),
        )


def parse_forecast_citations(text: str) -> Iterator[ParsedForecastCitation]:
    """Извлечь все forecast-цитаты в одном из двух v2.0.0 форматов."""
    for m in FORECAST_PATTERN.finditer(text):
        # Формат 1 (spec): `[Forecast: model, CI X%]`
        if m.group("model"):
            yield ParsedForecastCitation(
                model=m.group("model").strip(),
                ci=m.group("ci").strip(),
                raw=m.group(0),
            )
        # Формат 2 (реальный graph output): `[forecast_model:asset@horizon, ...]`
        elif m.group("asset"):
            method = (m.group("method") or "").strip()
            asset = m.group("asset").strip()
            horizon = m.group("horizon").strip()
            yield ParsedForecastCitation(
                model=method or f"{asset}@{horizon}",
                ci="",  # формат 2 не несёт CI в самой цитате
                raw=m.group(0),
            )


__all__ = [
    "RAG_PATTERN",
    "WEB_PATTERN",
    "FORECAST_PATTERN",
    "ParsedRagCitation",
    "ParsedWebCitation",
    "ParsedForecastCitation",
    "parse_rag_citations",
    "parse_web_citations",
    "parse_forecast_citations",
]
