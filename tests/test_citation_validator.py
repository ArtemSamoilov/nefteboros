"""Tests для nefteboros.citations.validator — функция validate().

Проверяет:
  - все цитаты подтверждены → valid=True, fabricated=[]
  - fabricated цитата (нет соответствия в источниках) → valid=False
  - missing_sources: источник был, не процитирован — не нарушает valid
  - duck-typing: validator работает с pydantic-моделями и dict'ами
  - mixed: RAG + Web + Forecast в одном ответе

Validator принимает Sequence[Any] для chunks/hits/calls — реальные тесты
используют lightweight dict'ы (без поднятия Chroma / Brave API).
"""
from __future__ import annotations

from nefteboros.citations import validate


# =============================================================================
# Helpers
# =============================================================================


def _chunk(source_title: str, page_start: int, page_end: int | None = None) -> dict:
    """Минимальный duck-type для Chunk."""
    return {
        "source_title": source_title,
        "page_start": page_start,
        "page_end": page_end if page_end is not None else page_start,
    }


def _hit(url: str, hostname: str, title: str = "") -> dict:
    """Минимальный duck-type для SearchHit."""
    return {"url": url, "hostname": hostname, "title": title}


def _forecast_call(method: str = "ensemble") -> dict:
    """Минимальный duck-type для ForecastResult."""
    return {"method": method, "asset": "brent", "horizon": "3m"}


# =============================================================================
# RAG validation
# =============================================================================


class TestRagValidation:
    def test_valid_when_chunk_matches_source_and_page(self) -> None:
        answer = "Согласно [OPEC MOMR март 2026, p.14] добыча..."
        chunks = [_chunk("OPEC MOMR март 2026", page_start=10, page_end=20)]
        report = validate(answer, retrieved_chunks=chunks)
        assert report.valid
        assert len(report.rag_citations) == 1
        assert report.fabricated == []

    def test_valid_with_substring_match_short_to_long(self) -> None:
        """LLM написал сокращённо — chunk имеет полное название."""
        answer = "[OPEC MOMR, p.14]"
        chunks = [_chunk("OPEC MOMR март 2026", page_start=14)]
        report = validate(answer, retrieved_chunks=chunks)
        assert report.valid

    def test_valid_with_page_range_overlap(self) -> None:
        """LLM привёл диапазон страниц — chunk покрывает один из них."""
        answer = "[Новатэк AR-2024, p.5-10]"
        chunks = [_chunk("Новатэк AR-2024", page_start=7, page_end=12)]
        report = validate(answer, retrieved_chunks=chunks)
        assert report.valid

    def test_fabricated_when_source_does_not_match(self) -> None:
        answer = "[Газпром Annual 2030, p.5]"
        chunks = [_chunk("OPEC MOMR март 2026", page_start=5)]
        report = validate(answer, retrieved_chunks=chunks)
        assert not report.valid
        assert len(report.fabricated) == 1
        assert "[Газпром Annual 2030, p.5]" in report.fabricated

    def test_fabricated_when_page_outside_range(self) -> None:
        answer = "[OPEC MOMR март 2026, p.99]"
        chunks = [_chunk("OPEC MOMR март 2026", page_start=1, page_end=50)]
        report = validate(answer, retrieved_chunks=chunks)
        assert not report.valid

    def test_chunks_without_page_metadata_pass(self) -> None:
        """Если у chunk нет page_start (None) — page-проверка пропускается."""
        answer = "[Source title, p.5]"
        chunks = [{"source_title": "Source title", "page_start": None, "page_end": None}]
        report = validate(answer, retrieved_chunks=chunks)
        assert report.valid

    def test_no_chunks_means_fabricated(self) -> None:
        """RAG-цитата без retrieved_chunks → fabricated."""
        answer = "[OPEC MOMR март 2026, p.14]"
        report = validate(answer, retrieved_chunks=[])
        assert not report.valid
        assert len(report.fabricated) == 1


# =============================================================================
# Web validation
# =============================================================================


class TestWebValidation:
    def test_valid_when_url_matches_hit(self) -> None:
        answer = "[News](https://reuters.com/article/123) — reuters.com, web"
        hits = [_hit("https://reuters.com/article/123", "reuters.com")]
        report = validate(answer, web_results=hits)
        assert report.valid
        assert len(report.web_citations) == 1

    def test_valid_when_hostname_matches_url_drift(self) -> None:
        """URL немного отличается (slash, query) — fallback на hostname."""
        answer = "[News](https://reuters.com/article/123/) — reuters.com, web"
        hits = [_hit("https://reuters.com/article/123", "reuters.com")]
        report = validate(answer, web_results=hits)
        assert report.valid  # via hostname fallback

    def test_fabricated_when_no_matching_hit(self) -> None:
        answer = "[News](https://malicious.example) — malicious.example, web"
        hits = [_hit("https://reuters.com/article", "reuters.com")]
        report = validate(answer, web_results=hits)
        assert not report.valid


# =============================================================================
# Forecast validation
# =============================================================================


class TestForecastValidation:
    def test_valid_when_forecast_call_present(self) -> None:
        answer = "Прогноз $80 [Forecast: ARIMA, CI 80%]."
        report = validate(answer, forecast_calls=[_forecast_call("sarimax")])
        assert report.valid
        assert len(report.forecast_citations) == 1

    def test_fabricated_when_no_forecast_call(self) -> None:
        """Forecast-цитата без вызова tool → fabricated."""
        answer = "Прогноз [Forecast: ARIMA, CI 80%]"
        report = validate(answer, forecast_calls=[])
        assert not report.valid


# =============================================================================
# Mixed
# =============================================================================


class TestMixedValidation:
    def test_all_three_types_valid(self) -> None:
        answer = (
            "[OPEC MOMR март 2026, p.14] и "
            "[News](https://reuters.com/x) — reuters.com, web. "
            "Прогноз: $82 [Forecast: ensemble, CI 80%]."
        )
        report = validate(
            answer,
            retrieved_chunks=[_chunk("OPEC MOMR март 2026", 10, 20)],
            web_results=[_hit("https://reuters.com/x", "reuters.com")],
            forecast_calls=[_forecast_call()],
        )
        assert report.valid
        assert len(report.rag_citations) == 1
        assert len(report.web_citations) == 1
        assert len(report.forecast_citations) == 1
        assert report.total_citations == 3

    def test_one_fabricated_marks_invalid(self) -> None:
        answer = (
            "[OPEC MOMR март 2026, p.14] (валидно) и "
            "[Fake source, p.99] (фейк)."
        )
        report = validate(
            answer,
            retrieved_chunks=[_chunk("OPEC MOMR март 2026", 10, 20)],
        )
        assert not report.valid
        assert len(report.fabricated) == 1
        assert "[Fake source, p.99]" in report.fabricated

    def test_missing_sources_does_not_break_valid(self) -> None:
        """Источник был извлечён, но не процитирован — valid=True, в missing."""
        answer = "[OPEC MOMR март 2026, p.14] (использовано)"
        chunks = [
            _chunk("OPEC MOMR март 2026", 10, 20),
            _chunk("IEA Oil 2025", 1, 100),  # не процитирован
        ]
        report = validate(answer, retrieved_chunks=chunks)
        assert report.valid
        assert "IEA Oil 2025" in report.missing_sources

    def test_forecast_call_not_cited_goes_to_missing(self) -> None:
        answer = "Просто ответ без forecast-цитаты."
        report = validate(answer, forecast_calls=[_forecast_call()])
        assert report.valid  # нет fabricated
        assert any("forecast" in m.lower() for m in report.missing_sources)


# =============================================================================
# Duck-typing: pydantic objects vs dicts
# =============================================================================


class _DuckChunk:
    """Псевдо-pydantic chunk через атрибуты."""

    def __init__(self, source_title: str, page_start: int, page_end: int) -> None:
        self.source_title = source_title
        self.page_start = page_start
        self.page_end = page_end


class TestDuckTyping:
    def test_validator_works_with_attribute_access(self) -> None:
        answer = "[OPEC MOMR март 2026, p.14]"
        chunks = [_DuckChunk("OPEC MOMR март 2026", 1, 50)]
        report = validate(answer, retrieved_chunks=chunks)
        assert report.valid
