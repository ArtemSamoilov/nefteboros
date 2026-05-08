"""Skill neftegaz_analyst — три независимых tools для нефтегаз-аналитика.

Tools (через PluginAPI v1):
  1. `analyst_query` — analyst LangGraph subgraph (forecast + synthesis).
     Тонкая обёртка над `nefteboros.graphs.analyst_graph`. Граф сам делает
     classify_intent + forecast() + synthesis с цитатами.
  2. `rag_search` — прямой retrieval из RAG-корпуса (802 chunks отчётов
     OPEC/IEA/EIA/корпоративка/санкции). Тонкая обёртка над
     `nefteboros.rag.retriever.Retriever`. Возвращает top-k chunks с метаданными.
  3. `web_search` — Brave Search API с tier-фильтрацией + auto language
     detection (RU-запрос → RU-источники, EN → EN). Тонкая обёртка над
     `nefteboros.search.WebSearcher`. Возвращает top-k результатов.

Агент в Ouroboros loop'е сам выбирает какой tool вызвать (или несколько) —
системный промпт прописывает приоритизацию из ТЗ §2.4 (RAG → web → forecast).

См.:
- ADR-0016 — `analyst_query` thin-wrapper design.
- ADR-0018 — `rag_search` tool, multi-tool architecture rationale.
- ADR-0022 — `web_search` tool, Brave + tier filter (этот PR).
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
# rag_search tool spec
# =============================================================================

_RAG_TOOL_DESCRIPTION = (
    "Поиск по корпусу нефтегазовых отчётов (RAG). 802 чанка из 25 документов:\n"
    "- стратегии РФ (Энергостратегия-2050, ИНЭИ, Минэк СЭР);\n"
    "- институциональные прогнозы (OPEC WOO, IEA Oil/Gas, GIIGNL, REPowerEU);\n"
    "- корпоративные отчёты РФ (Газпром, Роснефть, Лукойл, Новатэк, Татнефть — AR + IFRS);\n"
    "- operational срез рынка (OPEC MOMR, EIA STEO, IEA OMR/GMR, EI Stat Review);\n"
    "- геополитика (Bruegel WP по price cap, CRS по Ирану и Hormuz).\n\n"
    "ИСПОЛЬЗУЙ ПРИОРИТЕТНО на documentary вопросы:\n"
    "- факты из отчётов (доли рынка, объёмы добычи/экспорта, политика OPEC+);\n"
    "- стратегии и долгосрочные сценарии (целевые показатели, прогнозы 2030/2050);\n"
    "- санкции и геополитический контекст;\n"
    "- финансовые показатели компаний (P&L, cash flow, дивиденды).\n\n"
    "НЕ ИСПОЛЬЗУЙ на: текущие spot-цены, свежие новости (для них web), "
    "явные prediction-запросы (для них analyst_query/forecast).\n\n"
    "Возвращает JSON со списком top-k chunks: text, source_title, section_path, "
    "page_start/end, score. Цитируй источник в ответе как "
    "[Отчёт <source_title>, стр. <page>]."
)

_RAG_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Запрос на естественном языке (RU/EN). "
                "Например: «Что говорит OPEC про квоты в 2026?», "
                "«Стратегия Новатэка по СПГ-проектам», "
                "«Объём экспорта Газпрома в Китай»."
            ),
        },
        "k": {
            "type": "integer",
            "description": "Сколько top chunks вернуть (default 5, max 10).",
            "minimum": 1,
            "maximum": 10,
        },
    },
    "required": ["query"],
}

_RAG_DEFAULT_K = 5
_RAG_MAX_K = 10
_RAG_MAX_TEXT_CHARS = 4000  # на chunk — ограничение для tool response payload


# =============================================================================
# web_search tool spec
# =============================================================================

_WEB_TOOL_DESCRIPTION = (
    "Веб-поиск через Brave Search API для свежих рыночных данных:\n"
    "- spot-цены и текущие котировки;\n"
    "- свежие новости (заявления OPEC+, Минэнерго, Минфина, регуляторов);\n"
    "- комментарии и события последних дней / недель / месяцев.\n\n"
    "Язык запроса определяется автоматически по кириллице. Русский → "
    "RU-источники: Vedomosti / Kommersant / RBC / Interfax / TASS. "
    "Английский → EN-источники: Reuters / Bloomberg / FT / Argus / "
    "S&P Platts / Wood Mackenzie / OilPrice / OPEC.org / IEA.org / EIA.gov.\n\n"
    "ИСПОЛЬЗУЙ на: «текущая цена Brent», «что заявил Новак сегодня», "
    "«последние санкции против Газпрома», «спот WTI», «свежие новости рынка».\n\n"
    "НЕ ИСПОЛЬЗУЙ на: documentary факты из отчётов (для них rag_search), "
    "численные прогнозы цен (для них analyst_query). По ТЗ §2.4 порядок "
    "приоритета — RAG → web → forecast: сначала пробуй rag_search, "
    "если в корпусе нет — тогда web_search.\n\n"
    "Возвращает JSON: {results: [{title, url, hostname, tier, snippet, "
    "age, published}]}, где tier — 'tier1' (верифицированные деловые) / "
    "'tier2' (общие СМИ) / 'other'.\n\n"
    "Цитируй каждый источник markdown-ссылкой:\n"
    "  `[<title>](<url>) — <hostname>, web`\n"
    "Пример: `[OPEC keeps quotas](https://www.reuters.com/article/opec) "
    "— reuters.com, web`.\n"
    "Title / url / hostname — берутся ДОСЛОВНО из results[i]; никогда "
    "не выдумывай URL. Если url отсутствует, давай только hostname."
)

_WEB_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Поисковый запрос на естественном языке (RU/EN). Язык запроса "
                "определяет приоритетные источники (RU-запрос → RU-tier1). "
                "До 2000 символов."
            ),
        },
        "freshness": {
            "type": "string",
            "enum": ["pd", "pw", "pm", "py"],
            "description": (
                "Окно свежести: pd (past day) / pw (past week, default) / "
                "pm (past month) / py (past year)."
            ),
        },
        "k": {
            "type": "integer",
            "description": "Количество результатов (default 5, max 10).",
            "minimum": 1,
            "maximum": 10,
        },
        "tier": {
            "type": "string",
            "enum": ["all", "tier1"],
            "description": (
                "Фильтр источников: 'all' (default — tier1+tier2 без "
                "blacklist) или 'tier1' (только верифицированные деловые)."
            ),
        },
    },
    "required": ["query"],
}

_WEB_DEFAULT_K = 5
_WEB_MAX_K = 10
_WEB_MAX_SNIPPET_CHARS = 500
_WEB_VALID_FRESHNESS = frozenset({"pd", "pw", "pm", "py"})
_WEB_VALID_TIER = frozenset({"all", "tier1"})


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
# rag_search tool handler
# =============================================================================


def _serialize_rag_hit(hit: Any) -> dict[str, Any]:
    """RankedHit из retriever → JSON-friendly dict с метаданными для citation."""
    md = hit.metadata if isinstance(hit.metadata, dict) else {}
    text = (hit.text or "")
    # Обрезаем длинные chunks (table_only от EI Stat Review бывает 4000+ tokens).
    # Добавляем маркер `…` чтобы агент видел truncation.
    if len(text) > _RAG_MAX_TEXT_CHARS:
        text = text[:_RAG_MAX_TEXT_CHARS] + "\n\n[…усечено…]"
    return {
        "chunk_id": hit.chunk_id,
        "score": round(float(hit.rerank_score), 3),
        "text": text,
        "source_title": md.get("source_title") or md.get("source_id") or "(unknown)",
        "source_id": md.get("source_id", ""),
        "section_path": md.get("section_path", ""),
        "page_start": md.get("page_start") if isinstance(md.get("page_start"), int) and md.get("page_start", -1) >= 0 else None,
        "page_end": md.get("page_end") if isinstance(md.get("page_end"), int) and md.get("page_end", -1) >= 0 else None,
        "language": md.get("language", ""),
        "block": md.get("block", ""),
        "type": md.get("type", ""),
    }


def _tool_rag_search(*, query: str = "", k: int = _RAG_DEFAULT_K) -> str:
    """PluginAPI tool handler. Возвращает JSON-string.

    Resilient — на любых ошибках (Retriever/Chroma/embedder unavailable)
    возвращает JSON с error-полем, чтобы Ouroboros loop не падал.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return json.dumps({"error": "query is empty"}, ensure_ascii=False)
    if len(cleaned) > _MAX_QUERY_CHARS:
        return json.dumps(
            {"error": f"query too long: {len(cleaned)} chars (max {_MAX_QUERY_CHARS})"},
            ensure_ascii=False,
        )

    # Clamp k в [1, _RAG_MAX_K]
    try:
        k_int = int(k)
    except (TypeError, ValueError):
        k_int = _RAG_DEFAULT_K
    k_int = max(1, min(_RAG_MAX_K, k_int))

    # Lazy import — chromadb (~50 МБ) + sentence-transformers (~2 ГБ моделей).
    # На Ouroboros CI без domain stack модули могут отсутствовать → graceful.
    try:
        from nefteboros.rag.retriever import Retriever
    except ImportError as exc:
        logger.warning("Retriever unavailable: %s", exc)
        return json.dumps(
            {
                "error": (
                    f"RAG retriever unavailable: {type(exc).__name__}: {exc}. "
                    "Установи requirements-domain.txt и собери vectorstore "
                    "(scripts/build_index.py)."
                ),
            },
            ensure_ascii=False,
        )

    try:
        retriever = Retriever()
        hits = retriever.retrieve(
            cleaned,
            k_dense=max(30, k_int * 6),
            k_final=k_int,
        )
    except Exception as exc:  # noqa: BLE001 — tool handler must not crash
        logger.exception("rag_search retriever.retrieve failed")
        return json.dumps(
            {"error": f"RAG retrieval error: {type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )

    payload = {
        "query": cleaned,
        "k": k_int,
        "chunks": [_serialize_rag_hit(h) for h in hits],
        "total_returned": len(hits),
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


# =============================================================================
# web_search tool handler
# =============================================================================


def _serialize_web_hit(hit: Any) -> dict[str, Any]:
    snippet = hit.snippet or ""
    if len(snippet) > _WEB_MAX_SNIPPET_CHARS:
        snippet = snippet[:_WEB_MAX_SNIPPET_CHARS] + "…"
    return {
        "title": hit.title,
        "url": hit.url,
        "hostname": hit.hostname,
        "tier": hit.tier,
        "snippet": snippet,
        "age": hit.age,
        "published": hit.published,
    }


def _tool_web_search(
    *,
    query: str = "",
    freshness: str = "pw",
    k: int = _WEB_DEFAULT_K,
    tier: str = "all",
) -> str:
    """PluginAPI tool handler. Возвращает JSON-string.

    Resilient — не падает на сетевых ошибках / 429 / отсутствии ключа.
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

    try:
        k_int = int(k)
    except (TypeError, ValueError):
        k_int = _WEB_DEFAULT_K
    k_int = max(1, min(_WEB_MAX_K, k_int))

    if freshness not in _WEB_VALID_FRESHNESS:
        freshness = "pw"
    if tier not in _WEB_VALID_TIER:
        tier = "all"

    # Lazy import — httpx подтягивается, плюс свои модули. Делаем
    # только при реальном вызове, не при register(api).
    try:
        from nefteboros.search import BraveError, WebSearcher
    except ImportError as exc:
        logger.warning("WebSearcher unavailable: %s", exc)
        return json.dumps(
            {
                "error": (
                    f"web_search unavailable: {type(exc).__name__}: {exc}. "
                    "Установи httpx (requirements.txt)."
                ),
            },
            ensure_ascii=False,
        )

    searcher = WebSearcher()
    if not searcher.has_key:
        return json.dumps(
            {
                "error": (
                    "BRAVE_API_KEY not set. Get a key at brave.com/search/api/, "
                    "поставь его в .env."
                ),
            },
            ensure_ascii=False,
        )

    try:
        hits = searcher.search(
            cleaned, k=k_int, freshness=freshness, tier_filter=tier
        )
    except BraveError as exc:
        logger.warning("web_search Brave failure: %s", exc)
        return json.dumps(
            {"error": f"Brave search failed: {type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001 — tool handler must not crash
        logger.exception("web_search unexpected failure")
        return json.dumps(
            {"error": f"web_search runtime error: {type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )

    payload = {
        "query": cleaned,
        "k": k_int,
        "freshness": freshness,
        "tier_filter": tier,
        "total_returned": len(hits),
        "results": [_serialize_web_hit(h) for h in hits],
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
            "tools": ["analyst_query", "rag_search", "web_search"],
            "graph": "nefteboros.graphs.analyst_graph",
            "rag_retriever": "nefteboros.rag.retriever",
            "web_searcher": "nefteboros.search.WebSearcher",
        }
    )


# =============================================================================
# PluginAPI v1 entry point
# =============================================================================


def register(api: Any) -> None:
    """Загружается один раз при `load_extension`. Lazy import тяжёлых deps в handler'ах."""
    api.register_tool(
        "analyst_query",
        _tool_analyst_query,
        description=_TOOL_DESCRIPTION,
        schema=_TOOL_SCHEMA,
        timeout_sec=120,
    )
    api.register_tool(
        "rag_search",
        _tool_rag_search,
        description=_RAG_TOOL_DESCRIPTION,
        schema=_RAG_TOOL_SCHEMA,
        timeout_sec=30,
    )
    api.register_tool(
        "web_search",
        _tool_web_search,
        description=_WEB_TOOL_DESCRIPTION,
        schema=_WEB_TOOL_SCHEMA,
        timeout_sec=20,
    )
    api.register_route("health", _route_health, methods=("GET",))
    api.log(
        "info",
        "neftegaz_analyst: registered 3 tools "
        "(analyst_query, rag_search, web_search) + health route",
    )


__all__ = ["register"]
