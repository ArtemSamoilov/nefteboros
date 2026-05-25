"""Датаклассы self-reflection: нормализованный вид трейса и advisory-item.

См. ADR-0029-self-reflection.

**Граница безопасности (повторяется во всех модулях пакета):** self-reflection —
ADVISORY. Агент анализирует свою работу и *предлагает* улучшения, но НЕ применяет
их и НЕ переписывает себя. `BacklogEntry.applied` всегда `False` — ни одна функция
в кодовой базе не читает backlog обратно в контекст агента и не выставляет
`applied=True`. Это осознанная safety-граница для ассистента, работающего с
финансовыми данными (см. ADR-0029 §«Почему не auto-apply»).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

# Разрешённые значения — фиксируем словарём, валидируем мягко (out-of-vocab →
# normalize к дефолту), чтобы LLM-вывод с опечаткой не ронял пайплайн.
SEVERITIES = ("info", "low", "medium", "high")
CATEGORIES = (
    "routing",
    "citations",
    "refusal",
    "latency",
    "cost",
    "error",
    "coverage",
    "other",
)


@dataclasses.dataclass
class TraceView:
    """Нормализованный вид одного взаимодействия для рефлексии.

    Собирается из источника трейсов (JSONL/Langfuse) в `sources.py`. Поля
    заполняются по мере доступности — `query`/`answer` могут быть `None`, если
    источник их не несёт (live JSONL-tracer хранит query, но НЕ хранит текст
    ответа — он усечён до compact-метаданных, см. `tracer.py`). Детекторы
    деградируют по богатству трейса: без `answer` контентные сигналы
    (refusal/citation) пропускаются.
    """

    trace_id: str
    ts: Optional[str] = None
    query: Optional[str] = None
    answer: Optional[str] = None
    status: str = "ok"
    latency_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    nodes: list[str] = dataclasses.field(default_factory=list)
    error_nodes: list[str] = dataclasses.field(default_factory=list)
    span_count: int = 0

    @property
    def has_answer_text(self) -> bool:
        return bool(self.answer and self.answer.strip())


@dataclasses.dataclass
class ReflectionItem:
    """Одно advisory-наблюдение рефлексии (до записи в backlog).

    `source="llm"` — синтезировано LLM поверх агрегатов; `source="heuristic"` —
    детерминированный детектор (fallback, когда LLM недоступен). Оба честны и
    advisory.
    """

    observation: str
    suggestion: str
    severity: str = "info"
    category: str = "other"
    evidence_trace_id: Optional[str] = None
    source: str = "llm"

    def normalized(self) -> "ReflectionItem":
        """Привести severity/category к словарю (out-of-vocab → дефолт)."""
        sev = (self.severity or "").strip().lower()
        cat = (self.category or "").strip().lower()
        return dataclasses.replace(
            self,
            severity=sev if sev in SEVERITIES else "info",
            category=cat if cat in CATEGORIES else "other",
            observation=(self.observation or "").strip(),
            suggestion=(self.suggestion or "").strip(),
        )


@dataclasses.dataclass
class BacklogEntry:
    """Строка durable advisory-backlog'а (`data/self_improvement/backlog.jsonl`).

    `applied` — ВСЕГДА False на запись. Поле существует как явный маркер
    safety-границы: backlog advisory, человек в петле. Ни один код не выставляет
    его в True автоматически (см. модульный docstring)."""

    id: str
    date: str
    observation: str
    suggestion: str
    severity: str
    category: str
    fingerprint: str
    evidence_trace_id: Optional[str] = None
    status: str = "open"
    source: str = "llm"
    applied: bool = False

    def to_record(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


__all__ = [
    "SEVERITIES",
    "CATEGORIES",
    "TraceView",
    "ReflectionItem",
    "BacklogEntry",
]
