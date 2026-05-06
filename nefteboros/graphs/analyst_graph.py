"""Analyst LangGraph subgraph — wiring узлов в StateGraph.

См. ADR-0014 (minimal-graph baseline) + ADR-0015 (hybrid LLM disambiguate).

Граф:
    classify_intent (rule-based)
        ↓ _route_after_classify_initial
        ├─ refusal (rule_5/rule_3)              → synthesize
        ├─ forecast_simple / forecast_with_ctx  → forecast_call → synthesize
        └─ no_keyword_match                     → llm_disambiguate
                                                     ↓ _route_after_classify
                                                     ├─ refusal     → synthesize
                                                     └─ forecast    → forecast_call → synthesize
    synthesize → validate_citations → END

Build-функция возвращает скомпилированный StateGraph. Использование:

    graph = build_analyst_graph()
    final = await graph.ainvoke(GraphState(query="прогноз brent на 3 месяца"))
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from nefteboros.graphs.intents import classify_intent
from nefteboros.graphs.nodes import (
    forecast_call,
    llm_disambiguate,
    synthesize,
    validate_citations,
)
from nefteboros.graphs.state import GraphState, IntentType


# =============================================================================
# Узлы
# =============================================================================


async def _classify_node(state: GraphState) -> dict[str, Any]:
    """Async-обёртка над rule-based classify_intent — для consistency сигнатур."""
    intent = classify_intent(state.query)
    return {"intent": intent}


# =============================================================================
# Conditional routing
# =============================================================================


def _route_after_classify_initial(state: GraphState) -> str:
    """После rule-based classify: refusal | forecast | llm_disambiguate.

    Только `no_keyword_match` идёт в LLM. Refusal'ы из правил #5 и #3 —
    deterministic, без LLM (см. ADR-0015 §«Почему LLM только на no_keyword_match»).
    """
    if state.intent is None:
        # Defensive — _classify_node всегда ставит intent. Если не поставил,
        # bug graph wiring; идём в synthesize, чтобы пользователь увидел ответ.
        return "synthesize"
    if state.intent.matched_rule == "no_keyword_match":
        return "llm_disambiguate"
    if state.intent.type in (
        IntentType.RUSSIAN_GAS_REFUSAL,
        IntentType.OUT_OF_SCOPE,
    ):
        return "synthesize"
    return "forecast_call"


def _route_after_classify(state: GraphState) -> str:
    """После llm_disambiguate (или другого классификатора) — стандартный routing.

    Используется как conditional edge после `llm_disambiguate`. Не делает
    специальной логики на `no_keyword_match` — если LLM тоже не справился
    и оставил такой intent, направляет в synthesize (out_of_scope-ответ).
    """
    if state.intent is None:
        return "synthesize"
    if state.intent.type in (
        IntentType.RUSSIAN_GAS_REFUSAL,
        IntentType.OUT_OF_SCOPE,
    ):
        return "synthesize"
    return "forecast_call"


# =============================================================================
# Build
# =============================================================================


def build_analyst_graph() -> Any:
    """Собрать и скомпилировать analyst graph.

    Возвращаемое значение — CompiledStateGraph (LangGraph runtime); тип
    annotation Any потому что точная форма зависит от LangGraph версии,
    а наш код использует только публичный `.ainvoke(...)` интерфейс.
    """
    builder: StateGraph = StateGraph(GraphState)

    builder.add_node("classify_intent", _classify_node)
    builder.add_node("llm_disambiguate", llm_disambiguate)
    builder.add_node("forecast_call", forecast_call)
    builder.add_node("synthesize", synthesize)
    builder.add_node("validate_citations", validate_citations)

    builder.set_entry_point("classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
        _route_after_classify_initial,
        {
            "forecast_call": "forecast_call",
            "synthesize": "synthesize",
            "llm_disambiguate": "llm_disambiguate",
        },
    )
    builder.add_conditional_edges(
        "llm_disambiguate",
        _route_after_classify,
        {
            "forecast_call": "forecast_call",
            "synthesize": "synthesize",
        },
    )
    builder.add_edge("forecast_call", "synthesize")
    builder.add_edge("synthesize", "validate_citations")
    builder.add_edge("validate_citations", END)

    return builder.compile()


__all__ = ["build_analyst_graph"]
