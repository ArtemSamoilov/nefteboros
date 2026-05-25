"""Advisory improvement backlog — durable JSONL store.

См. ADR-0029-self-reflection. Восстановленная облегчённая версия выпиленного
`ouroboros/improvement_backlog.py`: сохранён принцип «advisory + dedup по
fingerprint + provenance», но ВЫРЕЗАНЫ (а) markdown-формат и (б) `format_*_digest`,
который инжектил backlog в контекст агента. Инжект в контекст — это ровно тот
механизм роста контекста, что вызвал хвостовой timeout в eval (ADR-0027), поэтому
он удалён намеренно.

**Граница безопасности:** запись односторонняя. Backlog пишется рефлексией и
читается ТОЛЬКО человеком/CLI (`scripts/self_reflect.py show-backlog`). Ни один
модуль агента (`server.py`, `ouroboros/*`, `nefteboros/graphs/*`) не импортирует
этот модуль и не читает backlog обратно в контекст. `applied` всегда False.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from nefteboros.self_reflection.schema import BacklogEntry, ReflectionItem

logger = logging.getLogger(__name__)

BACKLOG_REL_PATH = "data/self_improvement/backlog.jsonl"


def _repo_root() -> pathlib.Path:
    """Найти корень репозитория (.git/pyproject.toml) от cwd вверх."""
    cwd = pathlib.Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return cwd


def default_backlog_path() -> pathlib.Path:
    """Путь к backlog: env `OUROBOROS_SELF_REFLECTION_BACKLOG` или
    `<repo>/data/self_improvement/backlog.jsonl`."""
    env = os.environ.get("OUROBOROS_SELF_REFLECTION_BACKLOG", "").strip()
    if env:
        return pathlib.Path(env)
    return _repo_root() / BACKLOG_REL_PATH


def fingerprint(observation: str, suggestion: str, category: str) -> str:
    """Стабильный отпечаток для dedup. Нормализуем пробелы/регистр."""
    import re

    key = " | ".join(
        re.sub(r"\s+", " ", str(v or "")).strip().lower()
        for v in (observation, suggestion, category)
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_entries(path: Optional[pathlib.Path] = None) -> list[dict[str, Any]]:
    """Прочитать backlog. Битые строки пропускаются (graceful), не падаем."""
    path = path or default_backlog_path()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    logger.debug("skip malformed backlog line")
    except OSError as exc:
        logger.warning("cannot read backlog %s: %s", path, exc)
    return entries


def _existing_fingerprints(path: pathlib.Path) -> set[str]:
    return {
        str(e.get("fingerprint", ""))
        for e in load_entries(path)
        if e.get("fingerprint")
    }


def append_items(
    items: Iterable[ReflectionItem],
    *,
    path: Optional[pathlib.Path] = None,
    now: Optional[str] = None,
) -> int:
    """Дописать advisory-items в backlog. Dedup по fingerprint. Возвращает
    число РЕАЛЬНО добавленных записей.

    Запись advisory: `applied=False`, `status="open"`. Read-modify-append под
    best-effort flock (graceful, если ОС не поддерживает)."""
    path = path or default_backlog_path()
    items = [it.normalized() for it in items]
    items = [it for it in items if it.observation and it.suggestion]
    if not items:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = now or _now_iso()

    # Open в режиме a+ — создаёт файл при отсутствии, не усекает существующий.
    added = 0
    try:
        fh = path.open("a+", encoding="utf-8")
    except OSError as exc:
        logger.warning("cannot open backlog for write %s: %s", path, exc)
        return 0

    try:
        _flock(fh)
        seen = _existing_fingerprints(path)
        new_lines: list[str] = []
        for it in items:
            fp = fingerprint(it.observation, it.suggestion, it.category)
            if fp in seen:
                continue
            seen.add(fp)
            entry = BacklogEntry(
                id=f"sr-{fp}",
                date=stamp,
                observation=it.observation,
                suggestion=it.suggestion,
                severity=it.severity,
                category=it.category,
                fingerprint=fp,
                evidence_trace_id=it.evidence_trace_id,
                status="open",
                source=it.source,
                applied=False,  # никогда не True — safety-граница
            )
            new_lines.append(
                json.dumps(entry.to_record(), ensure_ascii=False, default=str)
            )
            added += 1
        if new_lines:
            fh.write("\n".join(new_lines) + "\n")
            fh.flush()
    finally:
        try:
            _unflock(fh)
        finally:
            fh.close()
    return added


def backlog_stats(path: Optional[pathlib.Path] = None) -> dict[str, Any]:
    """Сводка для CLI `status`: счётчики по статусу/severity/категории."""
    path = path or default_backlog_path()
    entries = load_entries(path)
    by_status: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for e in entries:
        by_status[str(e.get("status", "?"))] = by_status.get(str(e.get("status", "?")), 0) + 1
        by_severity[str(e.get("severity", "?"))] = by_severity.get(str(e.get("severity", "?")), 0) + 1
        by_category[str(e.get("category", "?"))] = by_category.get(str(e.get("category", "?")), 0) + 1
    dates = [str(e.get("date", "")) for e in entries if e.get("date")]
    return {
        "path": str(path),
        "total": len(entries),
        "open": by_status.get("open", 0),
        "applied": sum(1 for e in entries if e.get("applied")),
        "by_status": by_status,
        "by_severity": by_severity,
        "by_category": by_category,
        "last_date": max(dates) if dates else None,
    }


# --- best-effort advisory file lock (graceful на неподдерживающих ОС) ---


def _flock(fh: Any) -> None:
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass


def _unflock(fh: Any) -> None:
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass


__all__ = [
    "BACKLOG_REL_PATH",
    "default_backlog_path",
    "fingerprint",
    "load_entries",
    "append_items",
    "backlog_stats",
]
