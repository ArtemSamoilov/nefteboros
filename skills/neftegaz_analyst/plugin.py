"""Skill neftegaz_analyst — единый entry-point в analyst LangGraph subgraph.

Один tool `analyst_query` — тонкая обёртка над
`nefteboros.graphs.analyst_graph.build_analyst_graph().ainvoke(...)`.
Граф сам делает classify_intent (rule-based + GigaChat-2-Max LLM fallback),
вызов forecast(), synthesis с дисклеймерами и валидацию цитат.
Skill только expose'ит этот pipeline через PluginAPI v1.

См. ADR-0016 — рассуждение про thin-wrapper-design + lazy import тяжёлого
стека (pandas/statsmodels/yfinance/langgraph/langchain-gigachat).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


# =============================================================================
# Tool spec (видит LLM при tool selection)
# =============================================================================

_TOOL_DESCRIPTION = (
    "Старший аналитик нефтегазового рынка. Используй на вопросы про:\n"
    "- прогнозы цен (Brent, WTI, Urals, ESPO, urals_minfin_blend, "
    "Henry Hub, TTF, MOEXOG, GAZP, NVTK) на 1m / 3m / 6m / 12m;\n"
    "- бюджетную аналитику РФ (Минфин, НДПИ, нефтегаздоходы, налоговая "
    "формула 0.78×Urals + 0.22×ESPO);\n"
    "- сценарии нефтегазового рынка с дисклеймерами про неопределённость.\n\n"
    "Tool сам определяет intent (rule-based regex + GigaChat-2-Max LLM "
    "fallback на нерегулярные формулировки), вызывает forecast() и "
    "собирает ответ с цитатами и validation_warnings. Возвращает JSON.\n\n"
    "НЕ ИСПОЛЬЗУЙ на: погоду, акции вне нефтегаза, криптовалюты, общее "
    "общение, прогнозы курса валют. Tool вернёт out_of_scope."
)


_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Вопрос пользователя в естественном языке (русский или английский). "
                "Например: «прогноз Brent на 3 месяца», «сколько Минфин "
                "закладывает по нефти в бюджет 2026», «Bonny Light на квартал». "
                "Длина: до 2000 символов."
            ),
        },
    },
    "required": ["query"],
}


# Жёсткий лимит на длину запроса. Защита от случайного pasta-bombing'а.
_MAX_QUERY_CHARS = 2000


# =============================================================================
# Tool handler
# =============================================================================


def _serialize_intent(state: Any) -> dict[str, Any]:
    """Извлечь intent из final state как JSON-friendly dict."""
    intent_obj = state.get("intent") if isinstance(state, dict) else None
    if intent_obj is None:
        return {}
    return {
        "type": intent_obj.type.value,
        "matched_rule": intent_obj.matched_rule,
        "assets": list(intent_obj.forecast_assets),
        "horizon": (
            intent_obj.forecast_horizon.value
            if intent_obj.forecast_horizon is not None
            else None
        ),
        "refuse_reason": intent_obj.refuse_reason,
    }


def _serialize_citations(state: Any) -> list[dict[str, Any]]:
    citations = state.get("citations", []) if isinstance(state, dict) else []
    return [c.model_dump() for c in citations]


def _tool_analyst_query(*, query: str = "") -> str:
    """PluginAPI tool handler. Возвращает JSON-string.

    Resilient — не падает на graph error / LLM error / forecast error.
    Возвращает JSON с error-полем, чтобы Ouroboros loop увидел ошибку,
    но не валился.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return json.dumps({"error": "query is empty"}, ensure_ascii=False)
    if len(cleaned) > _MAX_QUERY_CHARS:
        return json.dumps(
            {
                "error": (
                    f"query too long: {len(cleaned)} chars (max {_MAX_QUERY_CHARS})"
                ),
            },
            ensure_ascii=False,
        )

    # Lazy import — heavy stack:
    # pandas/numpy/statsmodels/sklearn/yfinance + langgraph + langchain-gigachat
    # импортируется только при реальном вызове, не при register(api).
    try:
        from nefteboros.graphs.analyst_graph import build_analyst_graph
        from nefteboros.graphs.state import GraphState
    except ImportError as exc:
        logger.warning("analyst_graph unavailable: %s", exc)
        return json.dumps(
            {
                "error": (
                    f"analyst_graph unavailable: {type(exc).__name__}: {exc}. "
                    "Установи requirements-domain.txt."
                ),
            },
            ensure_ascii=False,
        )

    graph = build_analyst_graph()

    try:
        final = asyncio.run(graph.ainvoke(GraphState(query=cleaned)))
    except Exception as exc:  # noqa: BLE001 — tool handler must not crash
        logger.exception("analyst_query graph.ainvoke failed")
        return json.dumps(
            {"error": f"graph runtime error: {type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )

    payload = {
        "synthesis": (final.get("synthesis") if isinstance(final, dict) else "") or "",
        "intent": _serialize_intent(final),
        "citations": _serialize_citations(final),
        "validation_warnings": (
            list(final.get("validation_warnings", []))
            if isinstance(final, dict) else []
        ),
        "forecast_errors": (
            list(final.get("forecast_errors", []))
            if isinstance(final, dict) else []
        ),
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


# =============================================================================
# Healthcheck route
# =============================================================================


async def _route_health(request: Request) -> JSONResponse:
    """`GET /api/extensions/neftegaz_analyst/health`.

    Возвращает базовую информацию о skill'е. Не дёргает graph — это
    легковесный probe для liveness check.
    """
    return JSONResponse(
        {
            "status": "ok",
            "skill": "neftegaz_analyst",
            "tool": "analyst_query",
            "graph": "nefteboros.graphs.analyst_graph",
        }
    )


# =============================================================================
# PluginAPI v1 entry point
# =============================================================================


def register(api: Any) -> None:
    """Загружается один раз при `load_extension`. Lazy import графа в handler'е."""
    api.register_tool(
        "analyst_query",
        _tool_analyst_query,
        description=_TOOL_DESCRIPTION,
        schema=_TOOL_SCHEMA,
        timeout_sec=120,
    )
    api.register_route("health", _route_health, methods=("GET",))
    api.log(
        "info",
        "neftegaz_analyst: registered (analyst_query tool + health route)",
    )


__all__ = ["register"]
