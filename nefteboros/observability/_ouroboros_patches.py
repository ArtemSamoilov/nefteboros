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

import contextlib
import contextvars
import logging
import os
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_PATCHED: bool = False

# ContextVar для session_id текущего user-request. Устанавливается в
# `_patch_handle_task` при входе в handle_task; читается в `_patch_llm_client_chat`
# чтобы каждый LLM-call (включая safety supervisor через ouroboros core)
# получил правильный session_id даже если active OTel span был потерян на
# границе threading / context. Без этого safety supervisor traces появлялись
# как session=None, parent=None standalone roots.
_current_session_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "nefteboros_lf_session_id", default=None
)
_current_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "nefteboros_lf_user_id", default=None
)

# PID-based fallback для случаев когда ContextVar не propagated (ThreadPoolExecutor,
# Ouroboros тулы вызывают safety supervisor в worker thread без context inheritance).
# Один Ouroboros worker process обрабатывает один task за раз, поэтому per-PID
# state корректен для всех threads этого worker'а на время handle_task.
_session_per_pid: dict[int, str] = {}
_user_per_pid: dict[int, str] = {}

# TraceContext root span для cross-thread parent-child nesting. Все spans от
# safety supervisor / tool dispatch в worker threads подключаются как child
# к этому root, а не создают отдельные root traces. Без этого UI показывал
# многократно "user_request" как отдельные traces для одного chat-запроса.
_trace_context_per_pid: dict[int, dict[str, str]] = {}

# Последний финальный ответ агента (text от run_llm_loop). Patched
# `ouroboros.agent.run_llm_loop` сохраняет сюда text перед возвратом, и
# patched handle_task берёт оттуда для root_span.update(output=text). Без
# этого root_span получал fallback `{'events_count': N}` от
# `_extract_final_answer` — список pending_events не содержит plain
# assistant-текста (он передаётся через emit_progress / event_queue к UI).
_last_text_per_pid: dict[int, str] = {}


def get_active_trace_context() -> Optional[dict[str, str]]:
    """Достать TraceContext текущего worker process для cross-thread propagation.

    Используется traced_tool / patched chat в worker threads (где OTel
    ContextVars потеряны), чтобы повесить их span'ы как child main
    user_request span. Возвращает None если handle_task не активен.
    """
    return _trace_context_per_pid.get(os.getpid())


@contextlib.contextmanager
def remote_parent_cm(tc: Optional[dict[str, str]]) -> Iterator[None]:
    """Активирует remote parent OTel span БЕЗ AS_ROOT-флага Langfuse.

    Workaround для Langfuse 4.x: `client.start_as_current_observation(
    trace_context=tc, ...)` автоматически ставит `langfuse.internal.as_root=
    True` на создаваемый span (см. langfuse/_client/client.py). Сервер при
    rendering Tracing list выбирает trace.name / trace.input / trace.output
    от **worker** span'а с AS_ROOT (web_search), вместо корневого
    user_request — в UI колонки "Name/Input/Output" показывают tool вместо
    финального ответа агента.

    Fix: пробрасывать parent через OTel low-level `use_span(NonRecordingSpan)`
    — иерархия parent_observation_id остаётся (web_search.parent =
    user_request), но AS_ROOT не ставится. Сервер берёт metadata от root_span.

    Если tc пустой / некорректный — yield без эффекта (span будет создан как
    independent root в новом trace, как при handle_task off).
    """
    if not tc or "trace_id" not in tc or "parent_span_id" not in tc:
        yield
        return

    # Build parent OTel context. Если setup fails — yield без него (graceful
    # degrade). КРИТИЧНО: только ОДИН yield в generator-CM. Старая версия
    # имела два yield (в try и в except) → если downstream throws exception
    # после первого yield, generator при `__exit__` пытался re-yield во
    # втором — `RuntimeError: generator didn't stop after throw()`. Эта
    # ошибка ломала observability state на всех последующих request'ах в
    # процессе. См. fix observability fragility.
    non_rec = None
    try:
        from opentelemetry import trace as _otel_trace_api
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

        parent_ctx = SpanContext(
            trace_id=int(tc["trace_id"], 16),
            span_id=int(tc["parent_span_id"], 16),
            is_remote=True,
            trace_flags=TraceFlags(0x01),
        )
        non_rec = NonRecordingSpan(parent_ctx)
    except Exception:
        logger.debug(
            "remote_parent_cm: failed to build parent context, span будет создан без parent"
        )

    if non_rec is None:
        yield
        return

    with _otel_trace_api.use_span(non_rec, end_on_exit=False):
        yield


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


def _extract_final_answer(events: Any) -> Any:
    """Вытащить финальный ответ агента из result `handle_task`.

    `OuroborosAgent.handle_task` → `run_llm_loop` возвращает
    `list[Dict[str, Any]]` — события loop'а. Финальный ответ — последнее
    assistant-сообщение (без tool_calls). Используется для записи в
    `output` root user_request span'а.
    """
    if not isinstance(events, list):
        return events  # на всякий случай отдаём как есть
    # Ищем с конца assistant message с непустым content и без tool_calls.
    for ev in reversed(events):
        if not isinstance(ev, dict):
            continue
        if ev.get("role") != "assistant":
            continue
        if ev.get("tool_calls"):
            continue
        content = ev.get("content")
        if isinstance(content, str) and content.strip():
            return content
    # Fallback: вернуть compact summary всех events.
    return {"events_count": len(events)}


def _patch_run_llm_loop() -> None:
    """Перехватить `run_llm_loop` для извлечения финального text-ответа.

    `OuroborosAgent.handle_task` зовёт `run_llm_loop(...)` и получает
    `(text, usage, llm_trace)`. После этого text идёт в `emit_task_results`
    → event_queue → WS клиенту, но В RETURN VALUE handle_task НЕ попадает
    (возвращается только `self._pending_events`). Поэтому fallback
    `_extract_final_answer` на _pending_events возвращал
    `{"events_count": N}` — UI Tracing list "Output" column показывал
    мусор вместо реального ответа.

    Patched версия сохраняет text per-PID; patched handle_task потом
    читает его и кладёт в root_span.update(output=...).
    """
    try:
        import ouroboros.agent as agent_mod
        original = agent_mod.run_llm_loop
    except Exception as exc:  # noqa: BLE001
        logger.debug("run_llm_loop patch skipped: %s", exc)
        return

    def patched(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        try:
            if isinstance(result, tuple) and len(result) >= 1:
                text = result[0]
                if isinstance(text, str) and text.strip():
                    _last_text_per_pid[os.getpid()] = text
        except Exception:
            pass
        return result

    agent_mod.run_llm_loop = patched  # type: ignore[attr-defined]
    logger.info("ouroboros: run_llm_loop wrapped (capture final text для trace.output)")


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

        # Установить ContextVar + PID-fallback. ContextVar работает для same-thread
        # call chains. PID-fallback покрывает worker threads (ThreadPoolExecutor)
        # которые НЕ inherit ContextVars (стандарт CPython): safety supervisor,
        # tool dispatch _run threads. Все threads одного worker process делят PID
        # и читают same session_id из _session_per_pid.
        session_id = propagate_kwargs.get("session_id")
        sess_token = _current_session_id.set(session_id)
        user_token = _current_user_id.set(None)
        pid = os.getpid()
        if session_id:
            _session_per_pid[pid] = session_id
        try:
            client = get_client()
            with client.start_as_current_observation(
                name="user_request",
                as_type="agent",
                input={"query": task_text} if task_text else None,
            ) as root_span:
                # Сохранить TraceContext для cross-thread propagation: все
                # safety / tool dispatch spans подключатся как child этого root,
                # а не создадут отдельные user_request traces.
                _trace_context_per_pid[pid] = {
                    "trace_id": root_span.trace_id,
                    "parent_span_id": root_span.id,
                }
                # Сбросить буфер last_text для этого pid — на случай если от
                # предыдущей задачи осталась запись (process reuse).
                _last_text_per_pid.pop(pid, None)

                with propagate_attributes(**propagate_kwargs):
                    result = original(self, task)

                # Финальный ответ агента: 1) text от run_llm_loop (через
                # patched _patch_run_llm_loop, см. выше), 2) fallback на
                # _extract_final_answer от pending_events. Записываем в
                # root_span.update(output=...) — Langfuse выводит в
                # trace.output автоматически (через AS_ROOT root span).
                # `update_trace` / `set_trace_io` НЕ вызываем — в Langfuse
                # 4.x первого нет, второй deprecated; нам достаточно
                # observation_output на root span'е (см. ADR/подтверждено
                # в d0882be NonRecordingSpan fix).
                final_text = _last_text_per_pid.pop(pid, None)
                if final_text:
                    output_payload: Any = {"answer": final_text}
                else:
                    fallback = _extract_final_answer(result)
                    output_payload = (
                        fallback
                        if isinstance(fallback, dict)
                        else {"answer": fallback}
                    )
                try:
                    root_span.update(output=output_payload)
                except Exception:  # noqa: BLE001
                    pass
                # NB: flush НЕ внутри `with start_as_current_observation`
                # block — span ещё open и trace **incomplete**. Перенесли
                # flush ниже, ПОСЛЕ exit context manager.
        except Exception:
            logger.exception(
                "handle_task observability wrapper failed; running without obs"
            )
            return original(self, task)
        else:
            # `with start_as_current_observation` закрылся → root span
            # finalized → trace полный → flush'аем для гарантии delivery
            # на коротких диалогах.
            #
            # NB: OTel `tracer_provider.force_flush()` ломал child trace
            # propagation для tool_dispatch worker threads (ThreadPoolExecutor)
            # — sync close pipeline на уровне provider'а обрывал OTel context
            # inheritance, и tool span'ы (rag_search, web_search, analyst_query)
            # становились orphan root traces вместо child user_request.
            # Trade-off: с force_flush refusal-path стабильно, но child
            # tools теряются → user_request пустой по содержанию. Без
            # force_flush — иерархия восстанавливается, цена: refusal-path
            # иногда теряется. Качественная иерархия > 100% refusal coverage,
            # см. changelog 2026-05-11-observability-post-span-flush.md.
            try:
                client.flush()
            except Exception:  # noqa: BLE001 — flush никогда не ломает request
                pass
            return result
        finally:
            try:
                _current_session_id.reset(sess_token)
                _current_user_id.reset(user_token)
            except (LookupError, ValueError):
                pass
            _session_per_pid.pop(pid, None)
            _user_per_pid.pop(pid, None)
            _trace_context_per_pid.pop(pid, None)

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
        # Manual wrap LLMClient.chat_async / chat — основной путь.
        # `_patch_openai_module()` (langfuse.openai instrumentor через
        # sys.modules) ОТКЛЮЧЁН — он создавал дубликат span ("ouroboros_chat"
        # + "OpenAI-generation") на один LLM-вызов. Manual wrap покрывает
        # всё что нужно с правильным именем / структурой.
        _patch_run_llm_loop()
        _patch_handle_task()
        _patch_llm_client_chat()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ouroboros patches failed: %s", exc)
    _PATCHED = True


def _patch_llm_client_chat() -> None:
    """Manual span wrap для `LLMClient.chat_async` / `LLMClient.chat`.

    `langfuse.openai` instrumentor (через sys.modules patch) зависит от
    import order: если ouroboros.llm импортировал openai ДО нашего patch,
    то локальная ссылка `AsyncOpenAI` в namespace ouroboros.llm уже привязана
    к старому классу. Production может load openai раньше observability.

    Решение: обернуть `LLMClient.chat_async` / `chat` напрямую через
    `client.start_as_current_observation(...)` как context manager. Это
    надёжнее чем @observe декоратор (раньше ломал async OTel context для
    graph узлов) и не зависит от import order.

    Обёртка: открыть generation span до original вызова, после вернуть
    результат и обновить span с model / usage_details / cost_details из
    второго элемента tuple `(msg, usage)`.
    """
    try:
        from langfuse import get_client, propagate_attributes

        from ouroboros.llm import LLMClient
    except ImportError as exc:
        logger.debug("LLMClient chat patch skipped: %s", exc)
        return

    from nefteboros.observability.tracer import log_llm_usage

    original_async = LLMClient.chat_async
    original_sync = LLMClient.chat

    def _propagate_cm() -> Any:
        """Build propagate_attributes context из текущих ContextVars / PID-fallback.

        Ensures каждый chat call помечается session_id даже если active OTel
        parent span потерян. Lookup chain:
        1. ContextVar (same thread / async chain) — primary.
        2. _session_per_pid[os.getpid()] — fallback для ThreadPoolExecutor
           workers (safety supervisor, tool dispatch threads).

        Без этого safety supervisor traces получали session=None.
        """
        kwargs: dict[str, Any] = {"trace_name": "user_request"}
        sid = _current_session_id.get() or _session_per_pid.get(os.getpid())
        if sid:
            kwargs["session_id"] = sid
        uid = _current_user_id.get() or _user_per_pid.get(os.getpid())
        if uid:
            kwargs["user_id"] = uid
        return propagate_attributes(**kwargs)

    def _extract_usage_kwargs(usage: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if not isinstance(usage, dict):
            return kwargs
        model = usage.get("resolved_model") or usage.get("model")
        resolved: Optional[str] = None
        if model:
            # Strip провайдер-префикс ("openai-compatible/kimi-k2p6" → "kimi-k2p6")
            from nefteboros.observability.cost import _strip_provider_prefix

            resolved = _strip_provider_prefix(str(model))
            kwargs["model"] = resolved
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        usage_details: dict[str, int] = {}
        if prompt_tokens is not None:
            usage_details["input"] = int(prompt_tokens)
        if completion_tokens is not None:
            usage_details["output"] = int(completion_tokens)
        if usage_details:
            kwargs["usage_details"] = usage_details

        # Cost: либо pre-calculated в `usage["cost"]` (если LLMClient проставил),
        # либо вычисляем локально через nefteboros.observability.cost — иначе
        # Langfuse Cloud не знает наших нестандартных моделей (kimi-k2p6, glm-5,
        # GigaChat-2-Max) и оставляет cost=0. Зеркалит логику log_llm_usage в
        # nefteboros.observability.tracer.
        cost = usage.get("cost")
        if (
            cost is None
            and resolved
            and prompt_tokens is not None
            and completion_tokens is not None
        ):
            try:
                from nefteboros.observability.cost import compute_cost

                cached = int(usage.get("cached_tokens") or 0)
                cost = compute_cost(
                    resolved,
                    int(prompt_tokens),
                    int(completion_tokens),
                    cached,
                )
            except Exception:  # noqa: BLE001
                cost = None
        if cost is not None:
            kwargs["cost_details"] = {"total": float(cost)}
        return kwargs

    async def patched_async(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            client = get_client()
            messages = kwargs.get("messages") or (args[0] if args else None)
            # remote_parent_cm пробрасывает parent → child вложение БЕЗ AS_ROOT
            # на span. Иначе сервер Langfuse выбирает trace.name/input/output
            # от worker span'ов вместо main user_request root.
            tc = _trace_context_per_pid.get(os.getpid())
            with _propagate_cm():
                with remote_parent_cm(tc):
                    with client.start_as_current_observation(
                        name="ouroboros_chat",
                        as_type="generation",
                        input=messages,
                    ) as span:
                        result = await original_async(self, *args, **kwargs)
                        if isinstance(result, tuple) and len(result) == 2:
                            msg, usage = result
                            update_kwargs = _extract_usage_kwargs(usage)
                            if msg is not None:
                                update_kwargs["output"] = msg
                            if update_kwargs:
                                span.update(**update_kwargs)
                            # JSONL-tracer fallback (scope #2): продублировать
                            # cost/tokens в nefteboros _current_span — виден
                            # offline без Langfuse-ключей (compute_cost знает
                            # kimi/glm/GigaChat, Ouroboros pricing — нет → cost=0).
                            # Guard: ошибка tracer'а не должна ронять chat, иначе
                            # outer except пере-вызовет LLM (двойной счёт).
                            try:
                                if isinstance(usage, dict):
                                    log_llm_usage(usage)
                            except Exception:
                                logger.debug("log_llm_usage JSONL fallback failed", exc_info=True)
                        return result
        except Exception:
            # Любая ошибка observability → fallback на raw call.
            logger.debug("chat_async observability wrap failed; using raw")
            return await original_async(self, *args, **kwargs)

    def patched_sync(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            client = get_client()
            messages = kwargs.get("messages") or (args[0] if args else None)
            tc = _trace_context_per_pid.get(os.getpid())
            with _propagate_cm():
                with remote_parent_cm(tc):
                    with client.start_as_current_observation(
                        name="ouroboros_chat",
                        as_type="generation",
                        input=messages,
                    ) as span:
                        result = original_sync(self, *args, **kwargs)
                        if isinstance(result, tuple) and len(result) == 2:
                            msg, usage = result
                            update_kwargs = _extract_usage_kwargs(usage)
                            if msg is not None:
                                update_kwargs["output"] = msg
                            if update_kwargs:
                                span.update(**update_kwargs)
                            # JSONL-tracer fallback (scope #2) — см. patched_async.
                            try:
                                if isinstance(usage, dict):
                                    log_llm_usage(usage)
                            except Exception:
                                logger.debug("log_llm_usage JSONL fallback failed", exc_info=True)
                        return result
        except Exception:
            logger.debug("chat observability wrap failed; using raw")
            return original_sync(self, *args, **kwargs)

    LLMClient.chat_async = patched_async  # type: ignore[method-assign]
    LLMClient.chat = patched_sync  # type: ignore[method-assign]
    logger.info(
        "ouroboros: LLMClient.chat_async / chat wrapped (manual span, OTel-safe)"
    )


__all__ = ["apply_patches", "get_active_trace_context", "remote_parent_cm"]
