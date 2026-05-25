"""Оркестратор рефлексии: трейсы → сигналы → LLM-синтез → advisory backlog.

См. ADR-0029-self-reflection.

Цикл Ouroboros в advisory-форме (самореференция без самомодификации):
  наблюдение (трейсы) → рефлексия (LLM поверх паттернов) → backlog (предложения)
  → [человек ревьюит и решает].

LLM — первичный синтезатор; при его недоступности (нет ключей/сеть/ошибка)
graceful-откат на детерминированные `heuristic_items`. Оба пути дают РЕАЛЬНЫЙ
advisory-вывод. Ничего не применяется автоматически.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from typing import Any, Optional

from nefteboros.self_reflection import backlog as backlog_mod
from nefteboros.self_reflection.detectors import (
    Signals,
    compute_signals,
    heuristic_items,
)
from nefteboros.self_reflection.schema import CATEGORIES, SEVERITIES, ReflectionItem
from nefteboros.self_reflection.sources import load_recent_traces

logger = logging.getLogger(__name__)

_MAX_PROMPT_TRACES = 12
_EXCERPT = 220


def resolve_reflection_model() -> str:
    """Модель для рефлексии: `OUROBOROS_REFLECTION_MODEL` → `OUROBOROS_MODEL_LIGHT`
    → `SETTINGS_DEFAULTS`. Зеркалит `_resolve_fallback_model` advisory-ревью."""
    for env in ("OUROBOROS_REFLECTION_MODEL", "OUROBOROS_MODEL_LIGHT"):
        v = os.environ.get(env, "").strip()
        if v:
            return v
    try:
        from ouroboros.config import SETTINGS_DEFAULTS  # type: ignore

        return str(SETTINGS_DEFAULTS.get("OUROBOROS_MODEL_LIGHT", ""))
    except Exception:  # noqa: BLE001
        return ""


@dataclasses.dataclass
class ReflectionResult:
    source: str
    n_traces: int
    signals: Signals
    items: list[ReflectionItem]
    added: int
    backlog_path: str
    llm_used: bool
    note: str = ""


# =============================================================================
# LLM prompt + parse
# =============================================================================

_SYSTEM_PROMPT = (
    "Ты — рефлексивный аудитор работы AI-агента «нефтегазовый аналитик». "
    "Тебе дают агрегаты по недавним трейсам взаимодействий агента и выборку "
    "примеров. Найди паттерны слабых мест в работе агента: ложные отказы, "
    "отсутствие/слабость цитат, ошибки узлов графа, латентность, пробелы "
    "покрытия (ответ без опоры на источник), повторяющиеся проблемы. Предложи "
    "КОНКРЕТНЫЕ улучшения.\n\n"
    "ВАЖНО: ты НЕ применяешь изменения и НЕ переписываешь агента — только "
    "предлагаешь (advisory). Решение принимает человек.\n\n"
    "Верни СТРОГО JSON-массив объектов без пояснений:\n"
    '[{"observation": "...", "suggestion": "...", "severity": "info|low|medium|high", '
    '"category": "routing|citations|refusal|latency|cost|error|coverage|other", '
    '"evidence_trace_id": "<id из выборки или null>"}]\n'
    "Опирай observation на реальные числа/примеры. Текст observation и suggestion "
    "пиши по-русски. Если данных мало или проблем нет — верни []."
)


def _excerpt(text: Optional[str], n: int = _EXCERPT) -> str:
    if not text:
        return ""
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[:n] + "…"


def _sample_traces(traces: list, signals: Signals) -> list[dict[str, Any]]:
    """Выбрать информативные трейсы для промпта: сперва те, на которых есть
    флаги (находки), затем добор остальными. Кап `_MAX_PROMPT_TRACES`."""
    flagged_ids = {f.trace_id for f in signals.flags}
    flag_by_id: dict[str, list[str]] = {}
    for f in signals.flags:
        flag_by_id.setdefault(f.trace_id, []).append(f.kind)

    ordered = sorted(traces, key=lambda t: t.trace_id in flagged_ids, reverse=True)
    out: list[dict[str, Any]] = []
    for t in ordered[:_MAX_PROMPT_TRACES]:
        out.append(
            {
                "trace_id": t.trace_id,
                "query": _excerpt(t.query),
                "answer": _excerpt(t.answer) if t.has_answer_text else None,
                "nodes": t.nodes,
                "status": t.status,
                "latency_ms": t.latency_ms,
                "flags": sorted(set(flag_by_id.get(t.trace_id, []))),
            }
        )
    return out


def _build_messages(signals: Signals, sample: list[dict[str, Any]]) -> list[dict[str, str]]:
    user = (
        "Агрегаты по трейсам:\n"
        + json.dumps(signals.as_prompt_dict(), ensure_ascii=False, indent=2)
        + "\n\nВыборка трейсов:\n"
        + json.dumps(sample, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _extract_json_array(content: str) -> list[Any]:
    """Достать JSON-массив из ответа LLM (может быть обёрнут в прозу/```)."""
    if not content:
        return []
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(content[start : end + 1])
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _parse_items(content: str) -> list[ReflectionItem]:
    items: list[ReflectionItem] = []
    for obj in _extract_json_array(content):
        if not isinstance(obj, dict):
            continue
        obs = str(obj.get("observation", "")).strip()
        sug = str(obj.get("suggestion", "")).strip()
        if not obs or not sug:
            continue
        sev = str(obj.get("severity", "info")).strip().lower()
        cat = str(obj.get("category", "other")).strip().lower()
        ev = obj.get("evidence_trace_id")
        items.append(
            ReflectionItem(
                observation=obs,
                suggestion=sug,
                severity=sev if sev in SEVERITIES else "info",
                category=cat if cat in CATEGORIES else "other",
                evidence_trace_id=str(ev) if ev else None,
                source="llm",
            )
        )
    return items


def llm_reflect(
    traces: list,
    signals: Signals,
    *,
    model: Optional[str] = None,
    max_tokens: int = 1600,
) -> list[ReflectionItem]:
    """Один LLM-вызов поверх сигналов+выборки → advisory-items. Может бросить
    исключение (нет ключей/сеть) — вызывающий ловит и откатывается на heuristic.
    LLMClient импортируется лениво (чистый импорт пакета без тяжёлых зависимостей)."""
    from ouroboros.llm import LLMClient  # lazy: keep package import light + isolated

    model = model or resolve_reflection_model()
    if not model:
        raise RuntimeError("no reflection model resolved (set OUROBOROS_REFLECTION_MODEL)")

    messages = _build_messages(signals, _sample_traces(traces, signals))
    llm = LLMClient()
    response, _usage = llm.chat(
        messages=messages,
        model=model,
        reasoning_effort="low",
        max_tokens=max_tokens,
        temperature=0.0,
    )
    content = ""
    if isinstance(response, dict):
        content = str(response.get("content") or "")
    return _parse_items(content)


# =============================================================================
# Orchestration
# =============================================================================


def run_reflection(
    *,
    limit: int = 50,
    explicit_paths: Optional[list] = None,
    prefer_langfuse: bool = True,
    use_llm: bool = True,
    model: Optional[str] = None,
    backlog_path: Optional[Any] = None,
) -> ReflectionResult:
    """Полный цикл рефлексии. Никогда не бросает наружу из-за LLM/источника —
    деградирует graceful. Пишет advisory-items в backlog (dedup)."""
    traces, source = load_recent_traces(
        limit,
        explicit_paths=explicit_paths,
        prefer_langfuse=prefer_langfuse,
    )
    signals = compute_signals(traces)

    note = ""
    llm_items: list[ReflectionItem] = []
    if use_llm and traces:
        try:
            llm_items = llm_reflect(traces, signals, model=model)
            if not llm_items:
                note = "LLM не вернул структурированных находок; heuristic floor."
        except Exception as exc:  # noqa: BLE001
            note = f"LLM недоступен ({type(exc).__name__}: {exc}); fallback на heuristic."
            logger.info(note)

    heur = heuristic_items(signals)
    # LLM — первичный; heuristic — пол (floor), когда LLM пуст/недоступен.
    items = llm_items if llm_items else heur
    # llm_used честно отражает, что в backlog ушли ИМЕННО LLM-находки.
    llm_used = bool(llm_items)

    bpath = backlog_mod.default_backlog_path() if backlog_path is None else backlog_path
    added = backlog_mod.append_items(items, path=bpath)

    return ReflectionResult(
        source=source,
        n_traces=len(traces),
        signals=signals,
        items=items,
        added=added,
        backlog_path=str(bpath),
        llm_used=llm_used,
        note=note,
    )


__all__ = [
    "ReflectionResult",
    "resolve_reflection_model",
    "llm_reflect",
    "run_reflection",
]
