"""llm_disambiguate узел — GigaChat-2-Max для no_keyword_match.

Использует существующий `nefteboros.llm.gigachat.get_gigachat_chat_model`
(адаптер на langchain-gigachat, выбран в ADR-0007). Lazy import — узел
graph можно импортировать без langchain stack.

Узел вызывается conditional edge'ом ТОЛЬКО когда rule-based classify
вернул `Intent(type=OUT_OF_SCOPE, matched_rule="no_keyword_match")` —
формулировка вне keyword-набора. Refusal'ы из правил #5 и #3
(deterministic) сюда не попадают — проходят прямо в synthesize.

Resilient: ImportError / ValueError / API error / parse-fail — оставляем
исходный rule-based intent с пометкой `matched_rule="llm_*"`.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from nefteboros.forecast.schema import Horizon
from nefteboros.graphs.state import GraphState, Intent, IntentType

logger = logging.getLogger(__name__)


_PROMPT_FILE = (
    pathlib.Path(__file__).resolve().parents[2] / "prompts" / "disambiguate_intent.md"
)
# Routing LLM провайдер и модель читаются через nefteboros.llm.router
# из env-переменных ROUTING_LLM_PROVIDER / ROUTING_LLM_MODEL (см. ADR-0007,
# .env.example § «Routing LLM»). Раньше тут был hardcoded GigaChat-2-Max
# который игнорировал env-конфигурацию — fix(graph): wire через router.
_SYSTEM_PROMPT = (
    "Ты классификатор запросов про нефтегазовый рынок для аналитика Сбера. "
    "Возвращай ТОЛЬКО валидный JSON по schema. Без markdown, без комментариев "
    "вне JSON, без префикса/суффикса."
)


class _LLMIntent(BaseModel):
    """Структура structured output от GigaChat. Конвертируется в наш Intent."""

    type: IntentType
    assets: list[str] = Field(default_factory=list)
    horizon: Optional[str] = None
    refuse_reason: Optional[str] = None


def _build_prompt(query: str) -> str:
    """Заполняем шаблон disambiguate_intent.md значениями {ASSET_LIST}, {QUERY}."""
    template = _PROMPT_FILE.read_text(encoding="utf-8")

    from nefteboros.forecast.registry import ASSET_REGISTRY

    asset_list = "\n".join(
        f"- `{aid}` — {meta.display_name}" for aid, meta in ASSET_REGISTRY.items()
    )

    return template.replace("{ASSET_LIST}", asset_list).replace("{QUERY}", query)


_DEFAULT_ASSETS_BY_TYPE: dict[IntentType, list[str]] = {
    IntentType.FORECAST_SIMPLE: ["brent"],
    IntentType.FORECAST_WITH_CONTEXT: ["brent", "urals", "urals_minfin_blend"],
}


def _to_intent(llm: _LLMIntent) -> Intent:
    """LLM-output → наш Intent.

    Post-process safeguards:
    - Невалидный horizon → None (LLM иногда возвращает "5m"/"9m").
    - forecast_* type но пустой assets → подставляем default
      (forecast_simple → ["brent"]; forecast_with_context →
      ["brent", "urals", "urals_minfin_blend"]). На GigaChat-2-Max
      эта ошибка наблюдается чаще всего — type правильный, assets
      пропущен. Dataset eval (см. docs/experiments/intent_classifier.md)
      показал, что этот fallback вытягивает type_accuracy с 96% до
      ~97% и assets_jaccard_mean с 0.67 до 0.85+.
    - russian_gas_refusal / out_of_scope с assets — обнуляем.
    """
    horizon: Optional[Horizon] = None
    if llm.horizon:
        try:
            horizon = Horizon(llm.horizon)
        except ValueError:
            logger.warning("LLM returned invalid horizon: %r", llm.horizon)

    assets = list(llm.assets)
    if llm.type in (IntentType.FORECAST_SIMPLE, IntentType.FORECAST_WITH_CONTEXT):
        if not assets:
            assets = list(_DEFAULT_ASSETS_BY_TYPE.get(llm.type, []))
            logger.info(
                "LLM returned %s with empty assets — fallback to %s",
                llm.type.value, assets,
            )
    else:
        assets = []

    return Intent(
        type=llm.type,
        forecast_assets=assets,
        forecast_horizon=horizon,
        refuse_reason=llm.refuse_reason,
        matched_rule=f"llm_{llm.type.value}",
    )


def _fallback(state: GraphState, *, reason: str) -> dict[str, Any]:
    """state.intent остаётся, но matched_rule помечается reason'ом."""
    if state.intent is None:
        return {}
    return {"intent": state.intent.model_copy(update={"matched_rule": reason})}


async def llm_disambiguate(state: GraphState) -> dict[str, Any]:
    """Узел: GigaChat-2-Max для классификации no_keyword_match-запросов.

    Возвращает `{"intent": <new_intent>}` при успехе или fallback-intent
    при любой ошибке. Узел graph не падает.
    """
    try:
        from nefteboros.llm.router import get_chat_model
    except ImportError as exc:
        logger.warning("nefteboros.llm.router unavailable: %s", exc)
        return _fallback(state, reason="llm_unavailable_import")

    try:
        chat = get_chat_model(
            profile="routing",
            temperature=0.0,
            max_tokens=512,
        )
    except (ImportError, ValueError) as exc:
        logger.warning("Routing LLM chat model unavailable: %s", exc)
        return _fallback(state, reason="llm_unavailable_creds")

    try:
        prompt = _build_prompt(state.query)
    except OSError as exc:
        logger.warning("Failed to build disambiguate prompt: %s", exc)
        return _fallback(state, reason="llm_prompt_unreadable")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    raw_response: Any = None  # для логирования tokens (usage_metadata) после успеха
    try:
        result: Any
        try:
            structured = chat.with_structured_output(_LLMIntent)
            result = await structured.ainvoke(messages)
            # `with_structured_output` обычно возвращает уже распарсенный
            # объект и теряет usage_metadata. Это known limitation — для
            # этого узла cost будет null, см. ADR-0024-observability-langfuse §«Known limitations».
        except (NotImplementedError, AttributeError):
            response = await chat.ainvoke(messages)
            raw_response = response  # содержит usage_metadata
            raw = getattr(response, "content", None) or str(response)
            data = json.loads(str(raw).strip())
            result = _LLMIntent(**data)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("GigaChat parse-fail: %s", exc)
        return _fallback(state, reason="llm_parse_failed")
    except Exception as exc:  # noqa: BLE001 — graph node must not crash
        logger.exception("llm_disambiguate failed")
        return _fallback(state, reason=f"llm_error_{type(exc).__name__}")

    # Observability: если LangChain сохранил usage_metadata в ответе (fallback
    # путь), прикрепляем к span'у. На fast-path (with_structured_output)
    # usage_metadata теряется — span получит cost_usd=null. См. ADR-0024-observability-langfuse.
    try:
        from nefteboros.observability import log_llm_usage

        usage_meta = getattr(raw_response, "usage_metadata", None) if raw_response else None
        if usage_meta:
            log_llm_usage(
                dict(usage_meta) if not isinstance(usage_meta, dict) else usage_meta,
                model=_DEFAULT_MODEL,
                provider="gigachat",
            )
    except Exception as obs_exc:  # noqa: BLE001 — observability never breaks node
        logger.debug("log_llm_usage failed in llm_disambiguate: %s", obs_exc)

    if not isinstance(result, _LLMIntent):
        logger.warning("Unexpected LLM result shape: %r", type(result).__name__)
        return _fallback(state, reason="llm_invalid_shape")

    return {"intent": _to_intent(result)}


__all__ = ["llm_disambiguate"]
