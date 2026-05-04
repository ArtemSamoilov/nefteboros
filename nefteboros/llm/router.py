"""LLM router — выбор провайдера и модели.

API:
    get_chat_model(provider=None, model=None, **kwargs) -> BaseChatModel

Резолвинг (по убыванию приоритета):
    1. явные аргументы `provider`/`model`
    2. env `PRIMARY_LLM_PROVIDER` / `PRIMARY_LLM_MODEL`
    3. дефолт: gigachat / GigaChat-Max

Дополнительно поддерживается специальный provider `routing` — для быстрого
дешёвого узла classify_intent в LangGraph subgraph (читает `ROUTING_LLM_*` env).
"""

from __future__ import annotations

import os
from typing import Any, Optional


_VALID_PROVIDERS = {"gigachat", "hydra", "hydragpt"}


def _normalize_provider(value: str) -> str:
    value = value.strip().lower()
    if value == "hydragpt":
        return "hydra"
    return value


def get_chat_model(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    *,
    profile: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Вернуть chat model заданного провайдера.

    profile:
      None      — primary (default), читает PRIMARY_LLM_*
      "routing" — быстрый дешёвый, читает ROUTING_LLM_*
    """
    if profile == "routing":
        prov_env = "ROUTING_LLM_PROVIDER"
        model_env = "ROUTING_LLM_MODEL"
        prov_default = "hydra"
        model_default = "glm-5"
    else:
        prov_env = "PRIMARY_LLM_PROVIDER"
        model_env = "PRIMARY_LLM_MODEL"
        prov_default = "gigachat"
        model_default = "GigaChat-Max"

    raw_provider = provider or os.environ.get(prov_env, prov_default)
    resolved_provider = _normalize_provider(raw_provider)

    if resolved_provider not in _VALID_PROVIDERS - {"hydragpt"}:
        raise ValueError(
            f"Unknown LLM provider: {raw_provider!r}. "
            f"Valid: gigachat, hydra (alias: hydragpt)."
        )

    resolved_model = model or os.environ.get(model_env, model_default)

    if resolved_provider == "gigachat":
        from nefteboros.llm.gigachat import get_gigachat_chat_model
        return get_gigachat_chat_model(model=resolved_model, **kwargs)

    if resolved_provider == "hydra":
        from nefteboros.llm.hydra import get_hydra_chat_model
        return get_hydra_chat_model(model=resolved_model, **kwargs)

    # Не должно случиться (выше проверили _VALID_PROVIDERS)
    raise ValueError(f"Unhandled provider: {resolved_provider}")


__all__ = ["get_chat_model"]
