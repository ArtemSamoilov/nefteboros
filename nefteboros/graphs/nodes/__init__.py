"""Узлы analyst LangGraph subgraph.

См. ADR-0014 (graph design) + ADR-0015 (llm_disambiguate hybrid).

- forecast_call — вызов nefteboros.forecast.api.forecast() для активов из
  intent.forecast_assets (sequential).
- synthesize — финальный ответ через ouroboros.llm router; refusal без LLM.
- validate_citations — light regex pass над synthesis.
- llm_disambiguate — GigaChat-2-Max для no_keyword_match (вызывается только
  на запросах, которые rule-based classify не покрыл; см. ADR-0015).

Узлы для RAG / web — в feature/rag-integration / feature/web-integration.
"""

from nefteboros.graphs.nodes.forecast import forecast_call
from nefteboros.graphs.nodes.llm_disambiguate import llm_disambiguate
from nefteboros.graphs.nodes.synthesize import synthesize
from nefteboros.graphs.nodes.validate import validate_citations

__all__ = ["forecast_call", "llm_disambiguate", "synthesize", "validate_citations"]
