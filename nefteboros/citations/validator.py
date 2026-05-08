"""Citation validator — проверка соответствия цитат в ответе реальным
источникам (retrieved chunks / web hits / forecast calls).

Использование:

    from nefteboros.citations import validate

    report = validate(
        answer=agent_text,
        retrieved_chunks=chunks_from_rag,
        web_results=hits_from_brave,
        forecast_calls=forecast_results,
    )
    if not report.valid:
        log("fabricated citations: %s", report.fabricated)

Гарантии:

- Любая цитата в ответе агента в одном из трёх форматов (RAG / Web / Forecast)
  будет извлечена.
- Если для извлечённой цитаты нет соответствующего источника во входных
  данных — цитата помечается как ``fabricated``.
- Если в ``retrieved_chunks`` / ``web_results`` есть источник, но он не
  процитирован — это не ошибка валидации (`valid=True`), но фиксируется в
  ``missing_sources`` для structure adherence eval'а.

Гибкое matching (см. roadmap-v2.1 D6 раздел «Citation regex strictness»):

- **RAG.** Substring match в любую сторону между ``cite.source`` и
  ``chunk.source_title`` после lowercase. Покрывает кейсы где LLM пишет
  сокращённо («OPEC MOMR» вместо полного «OPEC MOMR март 2026») и
  где LLM приписывает дату к короткому source.
- **Page.** ``cite.page_start`` ∈ ``[chunk.page_start, chunk.page_end]``.
  Если у chunk нет page-метаданных (page_start is None) — page-проверка
  пропускается (такие chunks редкие, не блокируем валидацию из-за этого).
- **Web.** Точное равенство по URL (после strip + lowercase host); если
  url не найден — fallback на match по hostname.
- **Forecast.** Достаточно факта, что ``forecast_calls`` непустой
  (валидация модели name'а отложена — текущий v2.0.0 формат пишет
  ``ARIMA`` для метода ``sarimax``, надёжного mapping нет).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from nefteboros.citations.patterns import (
    ParsedForecastCitation,
    ParsedRagCitation,
    ParsedWebCitation,
    parse_forecast_citations,
    parse_rag_citations,
    parse_web_citations,
)


# =============================================================================
# Report
# =============================================================================


@dataclass
class CitationReport:
    """Итог валидации citations в ответе агента.

    ``valid=True`` ⇔ все цитаты в ответе подтверждены. ``missing_sources``
    не влияет на ``valid`` — это сигнал для structure adherence
    (агент не использовал доступные источники), не нарушение целостности.
    """

    valid: bool
    rag_citations: list[ParsedRagCitation] = field(default_factory=list)
    web_citations: list[ParsedWebCitation] = field(default_factory=list)
    forecast_citations: list[ParsedForecastCitation] = field(default_factory=list)
    fabricated: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)

    @property
    def total_citations(self) -> int:
        return (
            len(self.rag_citations)
            + len(self.web_citations)
            + len(self.forecast_citations)
        )


# =============================================================================
# Helpers — duck-typed access к Chunk / SearchHit / ForecastResult
# =============================================================================


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read attribute or dict key from ``obj``. Поддерживаем оба, чтобы
    validator работал и с pydantic-моделями, и с плоскими dict'ами."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalize(s: str) -> str:
    return s.strip().lower()


# =============================================================================
# Per-format matching
# =============================================================================


def _match_rag(
    cite: ParsedRagCitation,
    chunks: Sequence[Any],
) -> bool:
    """True если найден chunk с покрывающим source_title и page-range."""
    cite_source = _normalize(cite.source)
    if not cite_source:
        return False
    for ch in chunks:
        chunk_title = _normalize(_get(ch, "source_title", "") or "")
        if not chunk_title:
            continue
        # Substring в любую сторону — допускаем сокращение в обе стороны.
        if cite_source not in chunk_title and chunk_title not in cite_source:
            continue
        # Page-проверка — пропускаем если у chunk нет page-метаданных.
        chunk_page_start = _get(ch, "page_start")
        chunk_page_end = _get(ch, "page_end")
        if chunk_page_start is None or chunk_page_start < 0:
            return True  # source совпал, page недоступна — считаем подтверждённым
        if chunk_page_end is None or chunk_page_end < 0:
            chunk_page_end = chunk_page_start
        # Cited page (start или диапазон) должна пересекаться с chunk page-range.
        cite_pages = (
            range(cite.page_start, (cite.page_end or cite.page_start) + 1)
        )
        if any(chunk_page_start <= p <= chunk_page_end for p in cite_pages):
            return True
    return False


def _match_web(
    cite: ParsedWebCitation,
    hits: Sequence[Any],
) -> bool:
    """True если url или hostname совпадает с любым SearchHit."""
    cite_url = _normalize(cite.url)
    cite_host = _normalize(cite.hostname)
    for hit in hits:
        hit_url = _normalize(_get(hit, "url", "") or "")
        hit_host = _normalize(_get(hit, "hostname", "") or "")
        if cite_url and cite_url == hit_url:
            return True
        # Fallback на hostname — URL мог дрейфовать (трейлинговый слэш и т.п.).
        if cite_host and cite_host == hit_host:
            return True
    return False


def _match_forecast(
    cite: ParsedForecastCitation,
    calls: Sequence[Any],
) -> bool:
    """Forecast citation valid если был хоть один tool-call.

    Model name'ы не сверяем — текущий v2.0.0 формат пишет ``ARIMA`` для
    реальной модели ``sarimax``, и нет надёжного канонического mapping'а
    LLM-имён в ModelMethod-enum. Слабое место текущей итерации D6,
    отслеживается в roadmap (open question Track A / ADR-0023).
    """
    return len(list(calls)) > 0


# =============================================================================
# Public API
# =============================================================================


def validate(
    answer: str,
    *,
    retrieved_chunks: Sequence[Any] | None = None,
    web_results: Sequence[Any] | None = None,
    forecast_calls: Sequence[Any] | None = None,
) -> CitationReport:
    """Проверить все citations в ``answer`` против источников.

    Args:
        answer: финальный текст агента.
        retrieved_chunks: Sequence[Chunk | dict] с ``source_title`` /
            ``page_start`` / ``page_end``. None = «RAG не вызывался».
        web_results: Sequence[SearchHit | dict] с ``url`` / ``hostname``.
            None = «web не вызывался».
        forecast_calls: Sequence[ForecastResult | dict] (любой shape с
            фактом вызова). None = «forecast не вызывался».

    Returns:
        CitationReport с разбивкой по типам цитат, fabricated и missing.
    """
    chunks = retrieved_chunks or []
    hits = web_results or []
    calls = forecast_calls or []

    rag_cites = list(parse_rag_citations(answer))
    web_cites = list(parse_web_citations(answer))
    forecast_cites = list(parse_forecast_citations(answer))

    fabricated: list[str] = []

    for r in rag_cites:
        if not _match_rag(r, chunks):
            fabricated.append(r.raw)

    for w in web_cites:
        if not _match_web(w, hits):
            fabricated.append(w.raw)

    for f in forecast_cites:
        if not _match_forecast(f, calls):
            fabricated.append(f.raw)

    # Missing sources — какие из retrieved/web/forecast не использованы.
    cited_chunk_titles = {_normalize(r.source) for r in rag_cites}
    cited_urls = {_normalize(w.url) for w in web_cites}

    missing: list[str] = []
    for ch in chunks:
        title = _normalize(_get(ch, "source_title", "") or "")
        if not title:
            continue
        # missing если ни одна RAG-цитата не пересекается с этим chunk.
        if not any(c in title or title in c for c in cited_chunk_titles):
            missing.append(_get(ch, "source_title", ""))
    for hit in hits:
        url = _normalize(_get(hit, "url", "") or "")
        if url and url not in cited_urls:
            missing.append(_get(hit, "url", ""))
    if calls and not forecast_cites:
        missing.append("[forecast tool was called but not cited]")

    return CitationReport(
        valid=len(fabricated) == 0,
        rag_citations=rag_cites,
        web_citations=web_cites,
        forecast_citations=forecast_cites,
        fabricated=fabricated,
        missing_sources=missing,
    )


__all__ = ["CitationReport", "validate"]
