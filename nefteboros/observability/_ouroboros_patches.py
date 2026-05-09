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


def _patch_openai_module() -> None:
    """Подменить `openai.AsyncOpenAI` / `openai.OpenAI` на langfuse drop-in.

    `langfuse.openai.AsyncOpenAI` — instrumented версия которая автоматически
    создаёт `generation` observation для каждого `.chat.completions.create()`
    вызова. Capture'ит model, input messages, output content, usage tokens,
    cost. Работает на уровне HTTP-клиента (не как @observe декоратор) —
    не ломает OTel context propagation для async-узлов.

    Подмена в `sys.modules['openai']` применяется ДО того как ouroboros
    лениво импортирует `from openai import AsyncOpenAI` внутри
    `LLMClient._get_async_remote_client`. Все последующие импорты получат
    langfuse-wrapped версию.

    Это покрывает синтез финального ответа Ouroboros'ом для rag_search/
    web_search/analyst_query — каждый LLM-вызов попадает в Langfuse trace
    через активный `propagate_attributes` контекст из handle_task.

    Замечание: GigaChat через `langchain-gigachat` использует свой HTTP
    клиент, не openai SDK — покрытие не распространяется на него
    (llm_disambiguate). Кандидат на отдельный instrumentor.
    """
    try:
        import openai

        from langfuse.openai import AsyncOpenAI, OpenAI

        openai.AsyncOpenAI = AsyncOpenAI  # type: ignore[misc]
        openai.OpenAI = OpenAI  # type: ignore[misc]
        logger.info(
            "ouroboros: openai.AsyncOpenAI / OpenAI replaced with langfuse-wrapped versions"
        )
    except ImportError as exc:
        logger.debug("openai instrumentor patch skipped: %s", exc)


def _patch_handle_task() -> None:
    """Обернуть `OuroborosAgent.handle_task` в root observation +
    propagate_attributes.

    Без активного OTel-span'а `propagate_attributes` устанавливает atributes
    на КАЖДЫЙ новый trace отдельно — каждый openai-call (тысячи в одном
    user-request: list_tools / enable_tools / run_shell / synthesize / …)
    становится своим trace с теми же session_id и trace_name, но разными
    trace_ids. UI Sessions tab показывает кашу из десятков trace'ов.

    Решение: открыть ЯВНЫЙ root observation `user_request` через
    `start_as_current_observation(...)` ДО `propagate_attributes`. Все
    последующие openai-calls станут child этого root в одном trace.
    """
    try:
        from langfuse import get_client, propagate_attributes

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

        propagate_kwargs: dict[str, Any] = {"trace_name": "user_request"}
        if chat_id:
            propagate_kwargs["session_id"] = f"chat:{chat_id}"
        metadata: dict[str, Any] = {}
        if task_id:
            metadata["task_id"] = task_id
        if task_type:
            metadata["task_type"] = task_type
        if metadata:
            propagate_kwargs["metadata"] = metadata

        try:
            client = get_client()
            # Явный root span — без него каждый openai-call ходит в свой
            # trace, не группируется. С ним все child observations попадают
            # в один trace с собственно session_id и trace_name.
            with client.start_as_current_observation(
                name="user_request",
                as_type="agent",
                input={"query": task_text} if task_text else None,
            ):
                with propagate_attributes(**propagate_kwargs):
                    return original(self, task)
        except Exception:
            logger.exception(
                "handle_task observability wrapper failed; running without obs"
            )
            return original(self, task)

    OuroborosAgent.handle_task = patched  # type: ignore[method-assign]
    logger.info(
        "ouroboros: handle_task wrapped (root user_request + propagate_attributes)"
    )


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
        _patch_openai_module()
        _patch_handle_task()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ouroboros patches failed: %s", exc)
    _PATCHED = True


__all__ = ["apply_patches"]
