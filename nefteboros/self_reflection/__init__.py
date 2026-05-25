"""Self-reflection — advisory саморазвитие через рефлексию (НЕ самомодификацию).

См. ADR-0029-self-reflection (`docs/adr/0029-self-reflection.md`).

Агент наблюдает свою работу по трейсам, рефлексирует над паттернами и ПРЕДЛАГАЕТ
улучшения в durable backlog. Он НЕ применяет их и НЕ переписывает себя — человек в
петле. Это Ouroboros-цикл (самореференция) в безопасной advisory-форме, достаточной,
чтобы агент честно назывался саморазвивающимся, без риска самомодификации кода для
ассистента, работающего с финансовыми данными.

Триггер — CLI (`scripts/self_reflect.py`), НЕ каждый запрос. Управляется флагом
`OUROBOROS_SELF_REFLECTION` (default OFF). Прод (путь ответа агента) от пакета НЕ
зависит: ничего из `server.py` / `ouroboros/*` / `nefteboros/graphs/*` его не
импортирует и не читает backlog обратно в контекст.
"""

from __future__ import annotations

from nefteboros.self_reflection.backlog import (
    append_items,
    backlog_stats,
    default_backlog_path,
    load_entries,
)
from nefteboros.self_reflection.detectors import Signals, compute_signals
from nefteboros.self_reflection.reflect import (
    ReflectionResult,
    resolve_reflection_model,
    run_reflection,
)
from nefteboros.self_reflection.schema import (
    BacklogEntry,
    ReflectionItem,
    TraceView,
)
from nefteboros.self_reflection.sources import load_recent_traces

ENV_FLAG = "OUROBOROS_SELF_REFLECTION"


def is_enabled() -> bool:
    """Включена ли рефлексия. Default OFF — прод от неё не зависит."""
    import os

    return os.environ.get(ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


__all__ = [
    "ENV_FLAG",
    "is_enabled",
    "run_reflection",
    "ReflectionResult",
    "resolve_reflection_model",
    "compute_signals",
    "Signals",
    "load_recent_traces",
    "load_entries",
    "append_items",
    "backlog_stats",
    "default_backlog_path",
    "ReflectionItem",
    "TraceView",
    "BacklogEntry",
]
