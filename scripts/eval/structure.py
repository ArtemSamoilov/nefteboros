"""Structure adherence checker для ответов агента (D5).

Системный промпт ([prompts/SYSTEM.md](../../prompts/SYSTEM.md)) обязывает
структурированный ответ. D5 определяет три измеримых критерия,
оцениваемых **только по финальному тексту агента**:

1. **TL;DR** — первый параграф ≤2 предложений (либо весь ответ ≤2 предл.).
2. **Числовой факт** — хотя бы одно число в ответе (цена, %, объём, год).
3. **Citation в формате** — хотя бы одна цитата в формате RAG/Web/Forecast.

Не реализуется здесь:

- *Сверка цитат с tool outputs* — задача отдельного integration-eval'а
  (см. ``scripts/eval/eval_citations.py`` на ``citations_gold.jsonl``).
  E2E проверяет финальный итог, не глубину тулов.
- *Диапазон vs точка для price-related ответов* — требует D2 hedging.

Используется в [eval_e2e.py](eval_e2e.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from nefteboros.citations import (
    parse_forecast_citations,
    parse_rag_citations,
    parse_web_citations,
)

# =============================================================================
# Regex'ы
# =============================================================================

#: Числовой факт — любое число (целое, десятичное, с запятой как разделителем).
_NUMERIC_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")

#: Splitter предложений.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


# =============================================================================
# Report
# =============================================================================


@dataclass
class StructureReport:
    """Результат проверки structure adherence."""

    has_tldr: bool
    has_numeric_fact: bool
    has_citation: bool
    tldr_sentence_count: int
    numeric_count: int
    citation_count: int

    @property
    def passed(self) -> bool:
        return self.has_tldr and self.has_numeric_fact and self.has_citation


# =============================================================================
# Helpers
# =============================================================================


def _count_sentences(text: str) -> int:
    if not text.strip():
        return 0
    parts = [p for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    return len(parts)


def _first_paragraph(text: str) -> str:
    return text.strip().split("\n\n", 1)[0].strip()


# =============================================================================
# Public API
# =============================================================================


def check_structure(answer: str) -> StructureReport:
    """Проверить структурную адекватность ответа агента.

    Args:
        answer: финальный текст. Никаких tool outputs — e2e проверяет
            итоговый результат.

    Returns:
        StructureReport с per-criterion флагами и счётчиками.
    """
    # 1. TL;DR
    para_one = _first_paragraph(answer)
    tldr_sentences = _count_sentences(para_one)
    full_sentences = _count_sentences(answer)
    has_tldr = (0 < tldr_sentences <= 2) or (0 < full_sentences <= 2)

    # 2. Numeric fact
    numeric_matches = _NUMERIC_RE.findall(answer)
    numeric_count = len(numeric_matches)
    has_numeric_fact = numeric_count >= 1

    # 3. Citation в формате — через парсеры (без сверки с источниками).
    citation_count = (
        len(list(parse_rag_citations(answer)))
        + len(list(parse_web_citations(answer)))
        + len(list(parse_forecast_citations(answer)))
    )
    has_citation = citation_count >= 1

    return StructureReport(
        has_tldr=has_tldr,
        has_numeric_fact=has_numeric_fact,
        has_citation=has_citation,
        tldr_sentence_count=tldr_sentences,
        numeric_count=numeric_count,
        citation_count=citation_count,
    )


__all__ = ["StructureReport", "check_structure"]
