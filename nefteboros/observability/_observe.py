"""Optional `@observe` декоратор для domain-кода (RAG, search, etc).

Domain модули не должны иметь hard-dependency на langfuse SDK — он optional
зависимость. Этот helper:

1. Если SDK установлен — возвращает реальный `langfuse.observe`.
2. Если не установлен — возвращает no-op decorator (функция возвращается as-is).
3. Кеширует результат try-import — без повторных проверок на каждый вызов.

Использование в domain модуле:

    from nefteboros.observability._observe import observe

    @observe(name="embed_query", as_type="retriever")
    def embed_query(self, query: str) -> list[float]:
        ...

В Langfuse UI этот метод появится как child observation внутри
`propagate_attributes` контекста, открытого в `traced_tool` plugin.py.
Если LANGFUSE_ENABLED=false — метод выполняется без overhead'а.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_LF_OBSERVE: Optional[Callable[..., Any]] = None
_RESOLVED: bool = False


def _resolve() -> Optional[Callable[..., Any]]:
    """Lazy import langfuse.observe с feature-flag. Кешируется на process lifetime."""
    global _LF_OBSERVE, _RESOLVED
    if _RESOLVED:
        return _LF_OBSERVE

    flag = os.environ.get("LANGFUSE_ENABLED", "true").strip().lower()
    if flag in ("false", "0", "no", ""):
        _LF_OBSERVE = None
        _RESOLVED = True
        return None

    try:
        from langfuse import observe as _lf

        _LF_OBSERVE = _lf
    except ImportError:
        _LF_OBSERVE = None
    _RESOLVED = True
    return _LF_OBSERVE


def observe(
    *, name: Optional[str] = None, as_type: str = "span"
) -> Callable[[F], F]:
    """Optional `langfuse.observe` декоратор для domain-кода.

    Декорированная функция автоматически становится child observation в
    активном Langfuse trace (если есть). Без активного trace — no-op
    (Langfuse SDK создаст orphan trace, но это не критично для domain
    функций которые могут вызываться в eval / unit-тестах).

    Args:
        name: имя observation в Langfuse UI (default — fn.__name__).
        as_type: "span" / "generation" / "tool" / "retriever" / "chain" /
            "agent" / "evaluator" / "guardrail" / "embedding" — типизация в UI.
    """

    def decorator(fn: F) -> F:
        lf = _resolve()
        if lf is None:
            return fn  # no-op, без overhead'а
        return lf(name=name, as_type=as_type)(fn)  # type: ignore[no-any-return]

    return decorator


__all__ = ["observe"]
