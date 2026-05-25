"""Детерминированные детекторы поверх трейсов.

См. ADR-0029-self-reflection.

Считают РЕАЛЬНЫЕ сигналы из трейсов без LLM: error-rate, горячие узлы-источники
ошибок, latency/cost-перцентили, и контентные сигналы (refusal/citation) — но
только над трейсами, где есть текст ответа (Langfuse/sample; live JSONL его не
несёт, такие трейсы пропускаются — graceful деградация по богатству).

Сигналы используются двояко: (1) как вход для LLM-синтеза (`reflect.py`), (2) как
fallback-источник heuristic-items, если LLM недоступен. Оба пути — advisory.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Optional

from nefteboros.self_reflection.schema import ReflectionItem, TraceView

logger = logging.getLogger(__name__)

# Маркеры отказа (lowercase substrings). База — heuristic из eval_e2e
# (`scripts/eval/eval_e2e.py`) + расширение RU/EN. Намеренно консервативно:
# ложно-положительный отказ хуже пропуска, т.к. раздувает refusal-rate.
REFUSAL_MARKERS = (
    "запрос отклонён",
    "запрос отклонен",
    "запрос не покрыт",
    "вне доменной",
    "вне моей компетенции",
    "не в моей компетенции",
    "не могу помочь",
    "не могу ответить",
    "не располагаю информац",
    "i cannot help",
    "i can't help",
    "i'm unable to",
    "i am unable to",
    "i cannot answer",
)

# Узлы-инструменты: их наличие означает, что ответ опирался на источник.
_TOOL_NODES = (
    "rag_search",
    "web_search",
    "forecast_call",
    "retrieve",
    "embed_retrieve",
    "retrieval",
)


def looks_like_refusal(text: Optional[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in REFUSAL_MARKERS)


def has_citation(text: Optional[str]) -> bool:
    """Есть ли хоть одна цитата (RAG/Web/Forecast). Использует продовые парсеры
    из `nefteboros.citations`. Graceful, если модуль недоступен."""
    if not text:
        return False
    try:
        from nefteboros.citations import (
            parse_forecast_citations,
            parse_rag_citations,
            parse_web_citations,
        )
    except ImportError:
        return False
    return (
        any(parse_rag_citations(text))
        or any(parse_web_citations(text))
        or any(parse_forecast_citations(text))
    )


@dataclasses.dataclass
class Flag:
    """Точечная находка на конкретном трейсе — служит evidence для item'а."""

    trace_id: str
    kind: str
    detail: str


@dataclasses.dataclass
class Signals:
    n_traces: int = 0
    n_with_answer: int = 0
    error_rate: float = 0.0
    error_node_counts: dict[str, int] = dataclasses.field(default_factory=dict)
    latency_p50_ms: Optional[int] = None
    latency_p95_ms: Optional[int] = None
    cost_total_usd: float = 0.0
    refusal_rate: Optional[float] = None
    citation_rate: Optional[float] = None
    tool_skip_count: int = 0
    citation_node_gap_count: int = 0
    flags: list[Flag] = dataclasses.field(default_factory=list)

    def as_prompt_dict(self) -> dict:
        """Компактный JSON-вид для LLM-промпта (без сырых flags-объектов)."""
        return {
            "n_traces": self.n_traces,
            "n_with_answer_text": self.n_with_answer,
            "error_rate": round(self.error_rate, 3),
            "error_node_counts": self.error_node_counts,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "cost_total_usd": round(self.cost_total_usd, 6),
            "refusal_rate": None if self.refusal_rate is None else round(self.refusal_rate, 3),
            "citation_rate": None if self.citation_rate is None else round(self.citation_rate, 3),
            "tool_skip_count": self.tool_skip_count,
            "citation_node_gap_count": self.citation_node_gap_count,
        }


def _percentile(values: list[int], pct: float) -> Optional[int]:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[idx]


def compute_signals(traces: list[TraceView]) -> Signals:
    sig = Signals(n_traces=len(traces))
    if not traces:
        return sig

    n_error = 0
    latencies: list[int] = []
    answer_traces = 0
    refusals = 0
    cited = 0

    for t in traces:
        if t.status == "error":
            n_error += 1
        for node in t.error_nodes:
            sig.error_node_counts[node] = sig.error_node_counts.get(node, 0) + 1
            sig.flags.append(Flag(t.trace_id, "error_node", f"node={node}"))
        if t.latency_ms is not None:
            latencies.append(t.latency_ms)
        if t.cost_usd:
            sig.cost_total_usd += t.cost_usd

        # Отказ легитимно не требует инструмента/цитат — исключаем из структурных
        # прокси, когда можем его распознать (есть текст ответа). Иначе прокси
        # давали бы ложные срабатывания на каждом refusal'е.
        is_refusal = t.has_answer_text and looks_like_refusal(t.answer)
        # структурный прокси: synthesize без узла-инструмента → ответ без источника
        if "synthesize" in t.nodes and not is_refusal and not any(n in t.nodes for n in _TOOL_NODES):
            sig.tool_skip_count += 1
            sig.flags.append(
                Flag(t.trace_id, "tool_skip", "synthesize без retrieval/web/forecast")
            )
        # структурный прокси: synthesize без валидации цитат
        if "synthesize" in t.nodes and not is_refusal and "validate_citations" not in t.nodes:
            sig.citation_node_gap_count += 1
            sig.flags.append(
                Flag(t.trace_id, "citation_node_gap", "synthesize без validate_citations")
            )

        # контентные сигналы — только если есть текст ответа
        if t.has_answer_text:
            answer_traces += 1
            if looks_like_refusal(t.answer):
                refusals += 1
                sig.flags.append(Flag(t.trace_id, "refusal", _excerpt(t.answer)))
            elif not has_citation(t.answer):
                # non-refusal без цитат — потенциально неподкреплённый ответ
                sig.flags.append(Flag(t.trace_id, "no_citation", _excerpt(t.answer)))
            if has_citation(t.answer):
                cited += 1

    sig.error_rate = n_error / len(traces)
    sig.latency_p50_ms = _percentile(latencies, 50)
    sig.latency_p95_ms = _percentile(latencies, 95)
    sig.n_with_answer = answer_traces
    if answer_traces:
        sig.refusal_rate = refusals / answer_traces
        sig.citation_rate = cited / answer_traces
    return sig


def _excerpt(text: Optional[str], n: int = 160) -> str:
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= n else t[:n] + "…"


def _flag_for(sig: Signals, kind: str) -> Optional[str]:
    for f in sig.flags:
        if f.kind == kind:
            return f.trace_id
    return None


def heuristic_items(sig: Signals) -> list[ReflectionItem]:
    """Rule-based advisory items из сигналов — fallback, когда LLM недоступен.
    Каждый item привязан к реальному порогу и (по возможности) к evidence-трейсу.
    Пороги консервативны, чтобы не шуметь."""
    items: list[ReflectionItem] = []

    if sig.error_rate >= 0.10:
        sev = "high" if sig.error_rate >= 0.25 else "medium"
        items.append(
            ReflectionItem(
                observation=f"Error-rate {sig.error_rate:.0%} на выборке из {sig.n_traces} трейсов.",
                suggestion="Разобрать падающие узлы (см. error_node_counts) и добавить обработку/тесты на их входы.",
                severity=sev,
                category="error",
                evidence_trace_id=_flag_for(sig, "error_node"),
                source="heuristic",
            )
        )
    for node, cnt in sorted(sig.error_node_counts.items(), key=lambda kv: kv[1], reverse=True):
        if cnt >= 2:
            items.append(
                ReflectionItem(
                    observation=f"Узел '{node}' завершался ошибкой {cnt}× в выборке.",
                    suggestion=f"Локализовать причину сбоя '{node}'; добавить guard/ретрай или явный отказ вместо исключения.",
                    severity="medium",
                    category="error",
                    evidence_trace_id=_flag_for(sig, "error_node"),
                    source="heuristic",
                )
            )
    if sig.latency_p95_ms is not None and sig.latency_p95_ms >= 8000:
        items.append(
            ReflectionItem(
                observation=f"Хвостовая латентность p95={sig.latency_p95_ms}мс (p50={sig.latency_p50_ms}мс).",
                suggestion="Профилировать самые медленные узлы; рассмотреть кэш/таймаут на тяжёлых вызовах.",
                severity="low",
                category="latency",
                source="heuristic",
            )
        )
    if sig.refusal_rate is not None and sig.refusal_rate >= 0.30:
        items.append(
            ReflectionItem(
                observation=f"Refusal-rate {sig.refusal_rate:.0%} среди {sig.n_with_answer} ответов с текстом.",
                suggestion="Проверить, не отклоняет ли агент запросы в своей доменной области (ложные отказы); уточнить границы домена в SYSTEM.md.",
                severity="medium",
                category="refusal",
                evidence_trace_id=_flag_for(sig, "refusal"),
                source="heuristic",
            )
        )
    if sig.citation_rate is not None and sig.citation_rate < 0.60 and sig.n_with_answer >= 3:
        items.append(
            ReflectionItem(
                observation=f"Только {sig.citation_rate:.0%} ответов содержат цитаты ({sig.n_with_answer} ответов с текстом).",
                suggestion="Усилить требование цитирования в synthesize-промпте; проверить, доходят ли источники до финального ответа.",
                severity="medium",
                category="citations",
                evidence_trace_id=_flag_for(sig, "no_citation"),
                source="heuristic",
            )
        )
    if sig.citation_node_gap_count >= 1 and sig.n_traces >= 3:
        items.append(
            ReflectionItem(
                observation=f"{sig.citation_node_gap_count} трейсов прошли synthesize без узла validate_citations.",
                suggestion="Убедиться, что валидация цитат включена в граф для всех путей синтеза.",
                severity="low",
                category="citations",
                evidence_trace_id=_flag_for(sig, "citation_node_gap"),
                source="heuristic",
            )
        )
    if sig.tool_skip_count >= 1 and sig.n_traces >= 3:
        items.append(
            ReflectionItem(
                observation=f"{sig.tool_skip_count} трейсов синтезировали ответ без вызова инструмента (RAG/web/forecast).",
                suggestion="Проверить роутинг: возможны ответы без опоры на источник (риск неподкреплённого вывода).",
                severity="medium",
                category="coverage",
                evidence_trace_id=_flag_for(sig, "tool_skip"),
                source="heuristic",
            )
        )
    return items


__all__ = [
    "REFUSAL_MARKERS",
    "looks_like_refusal",
    "has_citation",
    "Flag",
    "Signals",
    "compute_signals",
    "heuristic_items",
]
