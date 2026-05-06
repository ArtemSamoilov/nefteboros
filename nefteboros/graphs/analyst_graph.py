"""Analyst LangGraph subgraph — wiring узлов в StateGraph (см. ADR-0014).

Граф:
    classify_intent
        ↓ (conditional edges)
        ├─ russian_gas_refusal / out_of_scope → synthesize (без forecast)
        └─ forecast_simple / forecast_with_context → forecast_call → synthesize
    synthesize → validate_citations → END

Build-функция возвращает скомпилированный StateGraph. Использование:

    graph = build_analyst_graph()
    final = await graph.ainvoke(GraphState(query="прогноз brent на 3 месяца"))
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from nefteboros.graphs.intents import classify_intent
from nefteboros.graphs.nodes import forecast_call, synthesize, validate_citations
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


def _route_after_classify(state: GraphState) -> str:
    """Conditional edge: после classify_intent — куда идём."""
    if state.intent is None:
        # Defensive — classify_node всегда ставит intent. Если не поставил —
        # это bug graph wiring; идём в synthesize чтобы пользователь увидел
        # хоть какой-то ответ.
        return "synthesize"
    if state.intent.type in (
        IntentType.RUSSIAN_GAS_REFUSAL,
        IntentType.OUT_OF_SCOPE,
    ):
        # Bypass forecast — выдадим refuse_reason из synthesize без LLM.
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
    builder.add_node("forecast_call", forecast_call)
    builder.add_node("synthesize", synthesize)
    builder.add_node("validate_citations", validate_citations)

    builder.set_entry_point("classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
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
