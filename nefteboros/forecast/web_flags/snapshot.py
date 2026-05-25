"""Версионируемое хранилище калибровочных snapshot'ов (ADR-0028).

Раскладка в `base_dir` (default `data/state/web_flags/`, override через
`NEFTEBOROS_WEB_FLAGS_DIR`):
  v0000.json, v0001.json, ...   — сериализованные CalibrationSnapshot (μ НЕ хранится)
  ACTIVE                        — номер активной версии
  changelog.jsonl               — полный diff-лог (propose / apply / reject)

Reproducibility: `forecast()` читает flag_states ИМЕННО активного snapshot, который
меняется только через approved-апдейт. Между апдейтами прогноз детерминирован.
Веб (детекция) дёргается отдельным триггером, не на каждый forecast; TTL активного
snapshot (`should_refresh`) подсказывает, когда пора перепроверять новости.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from nefteboros.forecast.web_flags.models import CalibrationSnapshot, now_iso

logger = logging.getLogger(__name__)

_DEFAULT_TTL_HOURS: int = 24


def _default_base_dir() -> Path:
    env = os.environ.get("NEFTEBOROS_WEB_FLAGS_DIR", "").strip()
    if env:
        return Path(env)
    # nefteboros/forecast/web_flags/snapshot.py → repo root = parents[3]
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "data" / "state" / "web_flags"


class SnapshotStore:
    """Файловое версионируемое хранилище активного калибровочного snapshot."""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else _default_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # --- пути ---
    def _version_path(self, version: int) -> Path:
        return self.base_dir / f"v{version:04d}.json"

    @property
    def _active_path(self) -> Path:
        return self.base_dir / "ACTIVE"

    @property
    def _changelog_path(self) -> Path:
        return self.base_dir / "changelog.jsonl"

    # --- версии ---
    def list_versions(self) -> list[int]:
        out = []
        for p in self.base_dir.glob("v*.json"):
            try:
                out.append(int(p.stem[1:]))
            except ValueError:
                continue
        return sorted(out)

    def get(self, version: int) -> CalibrationSnapshot:
        path = self._version_path(version)
        if not path.exists():
            raise FileNotFoundError(f"Snapshot version {version} not found in {self.base_dir}")
        return CalibrationSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def active_version(self) -> Optional[int]:
        if not self._active_path.exists():
            return None
        try:
            return int(self._active_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    def load_active(self) -> CalibrationSnapshot:
        """Активный snapshot. Если хранилище пусто — засевает seed (v0) и активирует."""
        v = self.active_version()
        if v is None:
            seed = CalibrationSnapshot.seed()
            return self._write(seed, set_active=True, log_action="seed")
        return self.get(v)

    def commit(
        self,
        snapshot: CalibrationSnapshot,
        *,
        set_active: bool = True,
        log_action: str = "apply",
    ) -> CalibrationSnapshot:
        """Записать НОВУЮ версию (следующий номер), опционально активировать + залогировать."""
        next_v = (max(self.list_versions()) + 1) if self.list_versions() else 0
        parent = self.active_version()
        committed = snapshot.model_copy(
            update={"version": next_v, "parent_version": parent, "as_of": snapshot.as_of or now_iso()}
        )
        return self._write(committed, set_active=set_active, log_action=log_action)

    def _write(
        self,
        snapshot: CalibrationSnapshot,
        *,
        set_active: bool,
        log_action: str,
    ) -> CalibrationSnapshot:
        self._version_path(snapshot.version).write_text(
            snapshot.model_dump_json(indent=2), encoding="utf-8"
        )
        if set_active:
            self._active_path.write_text(str(snapshot.version), encoding="utf-8")
        self.log_event(
            log_action,
            {
                "version": snapshot.version,
                "parent_version": snapshot.parent_version,
                "flag_states": snapshot.flag_states,
                "note": snapshot.note,
            },
        )
        logger.info("snapshot %s → v%d (active=%s)", log_action, snapshot.version, set_active)
        return snapshot

    def set_active(self, version: int) -> None:
        if not self._version_path(version).exists():
            raise FileNotFoundError(f"Cannot activate missing version {version}")
        self._active_path.write_text(str(version), encoding="utf-8")
        self.log_event("activate", {"version": version})

    # --- diff-лог ---
    def log_event(self, action: str, payload: dict[str, Any]) -> None:
        """Дописать строку в changelog.jsonl (полный аудит обновлений)."""
        entry = {"ts": now_iso(), "action": action, **payload}
        with self._changelog_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_log(self) -> list[dict[str, Any]]:
        if not self._changelog_path.exists():
            return []
        out = []
        for line in self._changelog_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    # --- TTL ---
    def should_refresh(self, ttl_hours: int = _DEFAULT_TTL_HOURS) -> bool:
        """True если активный snapshot старше TTL — пора перепроверять новости.

        Это управляет тем, КОГДА запускать детекцию (не на каждый forecast).
        """
        v = self.active_version()
        if v is None:
            return True
        try:
            as_of = datetime.fromisoformat(self.get(v).as_of)
        except (ValueError, OSError):
            return True
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - as_of).total_seconds() / 3600.0
        return age_h > ttl_hours


__all__ = ["SnapshotStore"]
