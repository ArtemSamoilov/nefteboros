"""validate_citations — light pass над synthesis.

В minimal-graph проверяем два инварианта:

1. Все [<...>] ссылки в synthesis должны соответствовать citations
   из state (LLM не выдумала источник).
2. Если synthesis опирается на forecast_results, там должна быть хотя
   бы одна `[forecast_*` ссылка.

Hallucination flag попадает в state.validation_warnings — это soft signal
для логирования / debug, не блокирует ответ. В integration PR'ах
(RAG/web) узел расширится: regex match по source_title + page для RAG,
URL whitelist для web и т.п.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nefteboros.graphs.state import GraphState

logger = logging.getLogger(__name__)


# `[<...>]` — простейший паттерн tag'а. Не nested.
_CITATION_RE = re.compile(r"\[([^\[\]]+?)\]")

# Не считаем cite'ом диапазоны/числа: «[$70.00, $101.00]», «[2024-01-01]» и т.п.
# Реальный citation tag начинается с буквы или подчёркивания (forecast_model, ADR-0012,
# OPEC WOO 2025, …) — ведущая цифра/символ валюты/тире — это форматирование, не источник.
_NON_CITATION_PREFIX = re.compile(r"^[\$€¥£\d\-+\s]")


async def validate_citations(state: GraphState) -> dict[str, Any]:
    """Light validation pass. Возвращает partial-update validation_warnings."""
    warnings: list[str] = []
    synthesis = state.synthesis or ""

    if not synthesis.strip():
        warnings.append("synthesis пустой — нечего валидировать.")
        return {"validation_warnings": warnings}

    expected_tags = {c.tag for c in state.citations}
    found_tags: set[str] = set()
    for match in _CITATION_RE.finditer(synthesis):
        inner = match.group(1)
        # Числовые диапазоны и значения CI — это форматирование, а не источник.
        if _NON_CITATION_PREFIX.match(inner):
            continue
        found_tags.add(f"[{inner}]")

    hallucinated = sorted(t for t in found_tags if t not in expected_tags)
    if hallucinated:
        warnings.append(
            "Возможно hallucinated citations (не среди state.citations): "
            + ", ".join(repr(t) for t in hallucinated)
        )

    if state.forecast_results and not any(
        t.startswith("[forecast_") for t in found_tags
    ):
        warnings.append(
            "synthesis содержит forecast результаты, но нет [forecast_*] ссылок."
        )

    return {"validation_warnings": warnings}


__all__ = ["validate_citations"]
