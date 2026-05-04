"""HydraGPT — OpenAI-совместимый шлюз к моделям Cloud.ru/JOI.

HydraGPT (`https://hydragpt.ru`) — российский шлюз, работающий из РФ без VPN
и предоставляющий доступ к моделям через две совместимые поверхности:

- `/v1/chat/completions` — OpenAI-совместимый
- `/v1/messages` — Anthropic-совместимый

Мы используем OpenAI-вариант через `langchain_openai.ChatOpenAI` с подменённым
`base_url`. Это даёт нам бесплатно: tool calling, structured output, streaming,
async, retries — всё что есть в langchain-openai адаптере.

Доступные модели (по `/models`): kimi-k2p6, kimi-k2p5, glm-5p1, glm-5,
deepseek-v4-pro, deepseek-v3p2, deepseek-v3p1, minimax-m2p7, gpt-oss-120b.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from langchain_openai import ChatOpenAI


HYDRA_DEFAULT_BASE_URL = "https://hydragpt.ru/v1"
HYDRA_DEFAULT_MODEL = "kimi-k2p6"


def get_hydra_chat_model(
    model: Optional[str] = None,
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout: int = 60,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs: Any,
) -> ChatOpenAI:
    """Создать LangChain ChatOpenAI, направленный на HydraGPT.

    Параметры читаются из env, если не передан override:
      HYDRA_API_KEY        — обязательно (формат `hydra_<32hex>`)
      HYDRA_BASE_URL       — по умолчанию https://hydragpt.ru/v1
      HYDRA_DEFAULT_MODEL  — по умолчанию kimi-k2p6

    `temperature=0.2` — дефолт под аналитика (минимизируем галлюцинации,
    но сохраняем естественный язык).
    `max_tokens=4096` — комфортный лимит для синтеза ответов с цитатами.
    """
    resolved_key = api_key or os.environ.get("HYDRA_API_KEY")
    if not resolved_key:
        raise ValueError(
            "HYDRA_API_KEY env not set. Get a token at @HydraGPTBot in Telegram, "
            "see docs/adr/0007-llm-providers.md."
        )
    resolved_base = base_url or os.environ.get("HYDRA_BASE_URL", HYDRA_DEFAULT_BASE_URL)
    resolved_model = model or os.environ.get("HYDRA_DEFAULT_MODEL", HYDRA_DEFAULT_MODEL)

    return ChatOpenAI(
        model=resolved_model,
        base_url=resolved_base,
        api_key=resolved_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        **kwargs,
    )


__all__ = ["get_hydra_chat_model", "HYDRA_DEFAULT_BASE_URL", "HYDRA_DEFAULT_MODEL"]
