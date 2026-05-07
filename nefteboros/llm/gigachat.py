"""GigaChat (Sber) LangChain adapter.

Использует официальный `langchain_gigachat` пакет. Поддерживает GigaChat Lite,
Pro, Max, Ultra (модель указывается через env `GIGACHAT_MODEL` или per-call).

Аутентификация: OAuth с client_id:client_secret в base64
(`GIGACHAT_CREDENTIALS`). Scope:
  - GIGACHAT_API_PERS — физлицо (default)
  - GIGACHAT_API_CORP — корпоративный
  - GIGACHAT_API_B2B  — B2B

Минцифры CA: GigaChat использует сертификат российского УЦ. По умолчанию
`verify_ssl_certs=False` (быстрый старт). Для prod — установить корневой
сертификат и `GIGACHAT_VERIFY_SSL=true`.
"""

from __future__ import annotations

import os
from typing import Any, Optional


def get_gigachat_chat_model(
    model: Optional[str] = None,
    *,
    credentials: Optional[str] = None,
    scope: Optional[str] = None,
    base_url: Optional[str] = None,
    auth_url: Optional[str] = None,
    temperature: float = 0.2,
    # max_tokens default берётся из ouroboros.config.get_max_output_tokens()
    # на стороне caller'а; здесь None означает «не передавать в langchain_gigachat,
    # пусть использует свой default». GigaChat имеет собственный output cap
    # (~16384 для Max), при превышении сам clamp'ит ответ.
    max_tokens: Optional[int] = None,
    timeout: int = 60,
    verify_ssl_certs: Optional[bool] = None,
    **kwargs: Any,
) -> Any:
    """Создать `langchain_gigachat.GigaChat` chat model.

    Параметры читаются из env, если не передан override:
      GIGACHAT_CREDENTIALS — base64(client_id:client_secret), обязательно
      GIGACHAT_SCOPE       — GIGACHAT_API_PERS | GIGACHAT_API_CORP | GIGACHAT_API_B2B
      GIGACHAT_MODEL       — GigaChat | GigaChat-Pro | GigaChat-Max | GigaChat-Ultra
      GIGACHAT_BASE_URL    — опционально, override
      GIGACHAT_AUTH_URL    — опционально, override
      GIGACHAT_VERIFY_SSL  — true/false (default: false для dev)

    Импорт `langchain_gigachat` отложенный — даёт лучшее сообщение об ошибке,
    если пакет не установлен (pip install langchain-gigachat).
    """
    try:
        from langchain_gigachat import GigaChat
    except ImportError as exc:
        raise ImportError(
            "langchain-gigachat не установлен. "
            "Добавь в requirements-domain.txt и сделай `pip install langchain-gigachat`."
        ) from exc

    resolved_creds = credentials or os.environ.get("GIGACHAT_CREDENTIALS")
    if not resolved_creds:
        raise ValueError(
            "GIGACHAT_CREDENTIALS env not set. Получи на developers.sber.ru/portal/products/gigachat-api, "
            "см. docs/adr/0007-llm-providers.md."
        )

    resolved_scope = scope or os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    resolved_model = model or os.environ.get("GIGACHAT_MODEL", "GigaChat-Max")

    if verify_ssl_certs is None:
        verify_ssl_certs = os.environ.get("GIGACHAT_VERIFY_SSL", "false").lower() in ("true", "1", "yes")

    init_kwargs: dict[str, Any] = {
        "credentials": resolved_creds,
        "scope": resolved_scope,
        "model": resolved_model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "verify_ssl_certs": verify_ssl_certs,
    }

    resolved_base = base_url or os.environ.get("GIGACHAT_BASE_URL")
    if resolved_base:
        init_kwargs["base_url"] = resolved_base

    resolved_auth = auth_url or os.environ.get("GIGACHAT_AUTH_URL")
    if resolved_auth:
        init_kwargs["auth_url"] = resolved_auth

    init_kwargs.update(kwargs)
    return GigaChat(**init_kwargs)


__all__ = ["get_gigachat_chat_model"]
