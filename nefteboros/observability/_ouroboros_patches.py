"""Monkey-patches Ouroboros для интеграции с Langfuse (Plan Y).

Цель: каждый user-request в Ouroboros agent loop = один Langfuse trace,
с session_id из chat_id, и каждый LLM-вызов (синтез ответа, рефлексия,
review) = generation observation внутри этого trace.

**Что патчим:**

1. `OuroborosAgent.handle_task(task)` — entry point per user-request.
   Оборачиваем в `langfuse.propagate_attributes(session_id=chat:<id>,
   trace_name="user_request", metadata={task_id, task_type})`. Все LLM
   вызовы и tool dispatches внутри унаследуют OTel context.

2. `LLMClient.chat_async` / `LLMClient.chat` — оборачиваем в
   `@langfuse.observe(as_type="generation")` + после вызова прокидываем
   `usage` (tokens/cost) через `client.update_current_generation`.

**Почему monkey-patch, не правка ouroboros/agent.py:**

Ouroboros — наследие upstream-форка (см. roadmap §«Принципы решений»),
правки в его коде усложняют будущий merge. Patch применяется при первом
импорте `nefteboros.observability` — minimal surface, легко откатить.

**Защита:**

- Применяется ТОЛЬКО если LANGFUSE_ENABLED=true (через `_try_import_langfuse`).
- Защита от double-patch через флаг `_PATCHED`.
- Все ошибки patch-а уходят в logger.warning, не падают.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_PATCHED: bool = False


def _enabled() -> bool:
    flag = os.environ.get("LANGFUSE_ENABLED", "true").strip().lower()
    return flag not in ("false", "0", "no", "")


def _patch_handle_task() -> None:
    """Обернуть `OuroborosAgent.handle_task` в propagate_attributes."""
    try:
        from langfuse import propagate_attributes

        from ouroboros.agent import OuroborosAgent
    except ImportError as exc:
        logger.debug("ouroboros patch skipped: %s", exc)
        return

    original = OuroborosAgent.handle_task

    def patched(self: Any, task: dict) -> Any:
        chat_id = task.get("chat_id") or 0
        task_id = str(task.get("id") or "")
        task_type = str(task.get("type") or "")
        task_text = str(task.get("text") or "")

        kwargs: dict[str, Any] = {"trace_name": "user_request"}
        if chat_id:
            kwargs["session_id"] = f"chat:{chat_id}"
        metadata: dict[str, Any] = {}
        if task_id:
            metadata["task_id"] = task_id
        if task_type:
            metadata["task_type"] = task_type
        if metadata:
            kwargs["metadata"] = metadata
        if task_text:
            # Tags не подходят (≤200 chars total), input_query попадает
            # в trace через первый LLM-call message[0].content. Здесь —
            # ничего дополнительного.
            pass

        try:
            with propagate_attributes(**kwargs):
                return original(self, task)
        except Exception:
            # Любая ошибка в propagate_attributes (network, OTel context) —
            # graceful: вызываем без observability.
            logger.exception("propagate_attributes wrapper failed; running task without obs")
            return original(self, task)

    OuroborosAgent.handle_task = patched  # type: ignore[method-assign]
    logger.info("ouroboros: OuroborosAgent.handle_task wrapped с propagate_attributes")


def _patch_llm_client() -> None:
    """Обернуть `LLMClient.chat_async` и `LLMClient.chat` в @observe(generation)
    + log_llm_usage из usage tuple."""
    try:
        from langfuse import observe

        from ouroboros.llm import LLMClient
    except ImportError as exc:
        logger.debug("LLMClient patch skipped: %s", exc)
        return

    from nefteboros.observability.tracer import log_llm_usage

    # --- chat_async ---
    original_async = LLMClient.chat_async

    @observe(name="ouroboros_chat", as_type="generation")
    async def patched_async(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = await original_async(self, *args, **kwargs)
        # Возврат tuple (msg, usage). Прокидываем usage в текущий generation.
        try:
            if isinstance(result, tuple) and len(result) == 2:
                _msg, usage = result
                if isinstance(usage, dict):
                    log_llm_usage(usage)
        except Exception as exc:  # noqa: BLE001
            logger.debug("log_llm_usage in chat_async patch failed: %s", exc)
        return result

    LLMClient.chat_async = patched_async  # type: ignore[method-assign]

    # --- sync chat ---
    original_sync = LLMClient.chat

    @observe(name="ouroboros_chat", as_type="generation")
    def patched_sync(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_sync(self, *args, **kwargs)
        try:
            if isinstance(result, tuple) and len(result) == 2:
                _msg, usage = result
                if isinstance(usage, dict):
                    log_llm_usage(usage)
        except Exception as exc:  # noqa: BLE001
            logger.debug("log_llm_usage in chat patch failed: %s", exc)
        return result

    LLMClient.chat = patched_sync  # type: ignore[method-assign]
    logger.info("ouroboros: LLMClient.chat_async / chat wrapped в @observe(generation)")


def apply_patches() -> None:
    """Apply all monkey-patches once. Idempotent.

    Вызывается из `nefteboros/observability/__init__.py` при первом импорте.
    Защита от double-patch через `_PATCHED`. Skip при LANGFUSE_ENABLED=false.
    """
    global _PATCHED
    if _PATCHED:
        return
    if not _enabled():
        _PATCHED = True
        return

    try:
        _patch_handle_task()
        # LLMClient patch отключён: оборачивание chat_async через @observe
        # ломало OTel context propagation для async graph узлов (analyst_query
        # терял synthesize/validate_citations spans). Plan Y v2: использовать
        # `langfuse.openai` instrumentor вместо @observe wrap на chat_async —
        # отложено до следующего PR (см. ADR-0025 §«Known limitations» после
        # обновления). Пока: handle_task propagate_attributes даёт session_id
        # / trace_name на agent loop, LLM-вызовы внутри получают auto-trace
        # только если они идут через декорированные узлы графа.
    except Exception as exc:  # noqa: BLE001
        logger.warning("ouroboros patches failed: %s", exc)
    _PATCHED = True


__all__ = ["apply_patches"]
