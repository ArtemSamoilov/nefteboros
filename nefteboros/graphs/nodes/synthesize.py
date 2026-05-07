"""synthesize узел.

Refusal intent (russian_gas_refusal / out_of_scope) — выдаёт
intent.refuse_reason без LLM-вызова (экономим токены).

Forecast intent — заполняет шаблон synthesize_forecast_only.md
forecast результатами и вызывает LLM через ouroboros.llm.LLMClient.

В integration PR'ах (RAG/web) узел расширится: prompt поменяется на
synthesize_with_overlay.md, появятся блоки RAG_CHUNKS / WEB_RESULTS.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import Any

from nefteboros.graphs.state import Citation, GraphState, IntentType

logger = logging.getLogger(__name__)


# nefteboros/graphs/nodes/synthesize.py → parents[2] = nefteboros/
_PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "prompts"
_SYSTEM_PROMPT_FILE = _PROMPTS_DIR / "system_analyst.md"
_USER_PROMPT_FILE = _PROMPTS_DIR / "synthesize_forecast_only.md"

_MODEL_ENV = "OUROBOROS_MODEL"
_MODEL_FALLBACK = "openai-compatible::kimi-k2p6"


def _read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read prompt %s: %s", path, exc)
        return ""


def _build_user_prompt(state: GraphState) -> str:
    template = _read_text(_USER_PROMPT_FILE)

    intent = state.intent
    if intent is None:
        intent_block = "Intent: NOT_CLASSIFIED (graph wiring bug)"
    else:
        horizon_str = (
            intent.forecast_horizon.value if intent.forecast_horizon else "default"
        )
        intent_block = (
            f"Intent: {intent.type.value} "
            f"(assets={intent.forecast_assets}, horizon={horizon_str}, "
            f"matched_rule={intent.matched_rule})"
        )

    if state.forecast_results:
        results_json = json.dumps(
            [r.model_dump(mode="json") for r in state.forecast_results],
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    else:
        results_json = "[]"

    if state.forecast_errors:
        errors_block = "\n".join(f"- {e}" for e in state.forecast_errors)
    else:
        errors_block = "(нет)"

    return (
        template.replace("{{QUERY}}", state.query)
        .replace("{{INTENT}}", intent_block)
        .replace("{{FORECAST_RESULTS_JSON}}", results_json)
        .replace("{{FORECAST_ERRORS}}", errors_block)
    )


def _build_citations(state: GraphState) -> list[Citation]:
    """Citations для forecast результатов (ForecastResult + ForecastRefusal).

    rag_chunk / web_url — расширятся в integration PR'ах.
    """
    citations: list[Citation] = []
    for result in state.forecast_results:
        method = getattr(result, "method", None)
        if method is None:
            # ForecastRefusal
            citations.append(
                Citation(
                    tag=f"[forecast_refusal:{result.asset}]",
                    kind="forecast_metadata",
                    detail="see ADR-0012 §«Горизонты прогноза»",
                )
            )
            continue
        horizon_value = getattr(result.horizon, "value", str(result.horizon))
        citations.append(
            Citation(
                tag=(
                    f"[forecast_model:{result.asset}@{horizon_value}, "
                    f"{method.value}, ADR-0012]"
                ),
                kind="forecast_model",
                detail=(
                    f"asset={result.asset}, method={method.value}, "
                    f"horizon={horizon_value}"
                ),
            )
        )
    return citations


async def _call_llm(system: str, user: str) -> str:
    """LLM call — graceful degradation на любых ошибках (узел не падает)."""
    try:
        from ouroboros.llm import LLMClient  # type: ignore[import-untyped]
    except ImportError as exc:
        logger.warning("ouroboros.llm не доступен: %s", exc)
        return "[LLM provider unavailable: ouroboros.llm import failed]"

    client = LLMClient()
    model = os.environ.get(_MODEL_ENV, _MODEL_FALLBACK)

    try:
        msg, _ = await client.chat_async(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            # Reasoning-style модели (Kimi-k2p6, GLM-5*) тратят значительную
            # долю output на скрытый ``delta.reasoning_content`` (CoT). При
            # 2048 токенов виsible ``content`` обрезается до неинформативной
            # шапки (174 chars) — agent интерпретирует это как «пустой
            # synthesis». 8192 даёт 1229 chars полного analytical ответа с
            # CI 80%/95% на forecast-prompt'е (~2k prompt_tokens). Stream-режим
            # в openai-compatible (см. ouroboros/llm.py chat_async) разблокирует
            # cap прокси Hydra на 4096.
            max_tokens=8192,
        )
    except Exception as exc:  # noqa: BLE001 — graph node must not crash
        logger.exception("synthesize: LLM call failed")
        return f"[LLM error: {type(exc).__name__}: {exc}]"

    content = (msg or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        return "[LLM returned empty content]"
    return content.strip()


async def synthesize(state: GraphState) -> dict[str, Any]:
    """Узел: финальный ответ.

    Refusal intent → intent.refuse_reason без LLM (экономия токенов).
    Forecast intent → LLM call через ouroboros.llm.
    """
    intent = state.intent

    if intent is not None and intent.type in (
        IntentType.RUSSIAN_GAS_REFUSAL,
        IntentType.OUT_OF_SCOPE,
    ):
        reason = intent.refuse_reason or "Запрос вне доменной области аналитика."
        return {
            "synthesis": reason,
            "citations": [],
        }

    user_prompt = _build_user_prompt(state)
    system_prompt = _read_text(_SYSTEM_PROMPT_FILE)

    answer = await _call_llm(system_prompt, user_prompt)
    citations = _build_citations(state)

    return {
        "synthesis": answer,
        "citations": citations,
    }


__all__ = ["synthesize"]
