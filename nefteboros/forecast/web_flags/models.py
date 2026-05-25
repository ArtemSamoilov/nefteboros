"""Доменные контракты слоя «новости → состояния флагов → μ» (ADR-0028, этап 2).

Закрытые enum'ы состояний драйверов выводятся НАПРЯМУЮ из `scenarios.DRIVERS`
(единый источник истины этапа 1) — классификатор не может разойтись с ключами,
которые понимает `compute_mu_from_flags`. μ в snapshot НЕ хранится: выводится
через `compute_mu_from_flags(asset, flag_states)` (этап 1), чтобы snapshot и
прогноз всегда считали одно и то же число.

См. ADR-0025 (детерминированная цепочка флаги→μ), ADR-0028 (веб-детекция,
approve-gate, guardrails, версионирование snapshot).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from nefteboros.forecast.scenarios import (
    DRIVER_BASE_STATES,
    DRIVERS,
    OIL_ASSETS,
    compute_mu_from_flags,
)

# Драйверы и их закрытые наборы состояний — ровно ключи этапа 1.
DRIVER_NAMES: tuple[str, ...] = tuple(DRIVERS.keys())


def valid_states(driver: str) -> tuple[str, ...]:
    """Закрытый enum состояний драйвера (ключи DRIVERS[driver]). Raises KeyError."""
    return tuple(DRIVERS[driver].keys())


def is_valid_state(driver: str, state: str) -> bool:
    return driver in DRIVERS and state in DRIVERS[driver]


def now_iso() -> str:
    """UTC ISO-8601 без микросекунд — стабильный ключ версии/as_of."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# =============================================================================
# Источник-цитата под одно состояние одного драйвера
# =============================================================================


class FlagSource(BaseModel):
    """Одна tier-1 цитата, поддерживающая конкретное состояние драйвера.

    Это аудит-след: какое СОБЫТИЕ (event_date) в каком ИСТОЧНИКЕ (hostname/url)
    LLM расценил как данное `state`. Число μ из этого НЕ считается — состояние
    идёт во вход детерминированной цепочки этапа 1.
    """

    model_config = ConfigDict(frozen=True)

    driver: str
    state: str
    hostname: str
    url: str
    title: str
    snippet: str = ""
    tier: str = "tier1"
    published: Optional[str] = None     # из выдачи Brave (page_age)
    event_date: Optional[str] = None    # дата события, извлечённая LLM
    confidence: float = Field(0.0, ge=0.0, le=1.0)


# =============================================================================
# Вердикт детекции по одному драйверу
# =============================================================================


class DriverDetection(BaseModel):
    """Итог детекции одного драйвера за прогон.

    `changed` истинно ТОЛЬКО когда новое состояние отличается от prior И
    подтверждено ≥2 различными tier-1 источниками (правило в `detect.py`).
    При конфликте источников — `disputed=True`, состояние НЕ меняется.
    """

    model_config = ConfigDict(frozen=True)

    driver: str
    prior_state: str
    detected_state: str
    changed: bool = False
    disputed: bool = False
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str = ""
    sources: list[FlagSource] = Field(default_factory=list)

    @property
    def tier1_support(self) -> int:
        """Сколько РАЗЛИЧНЫХ tier-1 хостов поддержали detected_state."""
        return len(
            {
                s.hostname
                for s in self.sources
                if s.tier == "tier1" and s.state == self.detected_state
            }
        )


# =============================================================================
# Версионируемый калибровочный snapshot
# =============================================================================


class CalibrationSnapshot(BaseModel):
    """Замороженное состояние флагов с аудит-следом источников.

    μ НЕ хранится — `mu(asset)` выводит его через цепочку этапа 1, поэтому
    одинаковый snapshot всегда даёт одинаковую μ (reproducibility). Прогноз
    читает ИМЕННО этот snapshot; веб дёргается только при обновлении.
    """

    as_of: str = Field(default_factory=now_iso)
    flag_states: dict[str, str]
    sources: dict[str, list[FlagSource]] = Field(default_factory=dict)
    version: int = 0
    parent_version: Optional[int] = None
    note: str = ""

    def mu(self, asset: str) -> float:
        """μ_0 для нефтяного актива по цепочке этапа 1 (не хранится)."""
        return compute_mu_from_flags(asset, self.flag_states)

    def mu_all(self) -> dict[str, float]:
        """μ_0 по всем нефтяным активам — для diff/guardrails."""
        return {a: compute_mu_from_flags(a, self.flag_states) for a in sorted(OIL_ASSETS)}

    @classmethod
    def seed(cls, note: str = "seed: snapshot 2026-05-08 (= ASSET_PARAMS base)") -> "CalibrationSnapshot":
        """Стартовый snapshot = текущее (base) состояние всех драйверов.

        Балансы = 0 ⇒ μ == замороженная база ASSET_PARAMS, т.е. seed-прогноз
        совпадает с дефолтным `forecast()` (обратная совместимость).
        """
        return cls(flag_states=dict(DRIVER_BASE_STATES), version=0, note=note)


# =============================================================================
# Предложение об изменении μ (approve-gate)
# =============================================================================


class AssetMuDelta(BaseModel):
    """Сдвиг μ одного актива между активным и предложенным snapshot."""

    model_config = ConfigDict(frozen=True)

    asset: str
    old_mu: float
    new_mu: float

    @property
    def delta(self) -> float:
        return self.new_mu - self.old_mu

    @property
    def pct(self) -> float:
        """|Δμ| как доля от старой μ (для Δμ-cap)."""
        if self.old_mu == 0:
            return 0.0
        return abs(self.new_mu - self.old_mu) / abs(self.old_mu)


class GuardrailReport(BaseModel):
    """Итог проверки guardrails предложенного перехода snapshot→snapshot."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    cap_pct: float
    max_observed_pct: float = 0.0
    invariant_ok: bool = True
    cap_violations: list[str] = Field(default_factory=list)
    invariant_violations: list[str] = Field(default_factory=list)

    @property
    def violations(self) -> list[str]:
        return [*self.cap_violations, *self.invariant_violations]


class MuProposal(BaseModel):
    """Предложение «новости → новая μ», ожидающее approve.

    Применяется к активному snapshot ТОЛЬКО после явного подтверждения
    (`propose.apply_proposal`). Никогда не молча.
    """

    base: CalibrationSnapshot
    proposed: CalibrationSnapshot
    changed_drivers: list[DriverDetection] = Field(default_factory=list)
    deltas: list[AssetMuDelta] = Field(default_factory=list)
    guardrail: GuardrailReport

    @property
    def has_changes(self) -> bool:
        return self.base.flag_states != self.proposed.flag_states

    def human_summary(self) -> str:
        """Человекочитаемое предложение: μ + причина + источники + guardrail."""
        if not self.has_changes:
            return "Изменений нет: детекция не дала подтверждённых (≥2 tier-1) переходов."
        lines: list[str] = ["Предложение обновления калибровки (требует подтверждения):"]
        for d in self.changed_drivers:
            cites = ", ".join(
                f"{s.hostname}"
                + (f" [{s.event_date}]" if s.event_date else "")
                for s in d.sources
                if s.state == d.detected_state
            )
            lines.append(
                f"  • {d.driver}: {d.prior_state} → {d.detected_state} "
                f"(уверенность {d.confidence:.0%}; источники: {cites or '—'})"
            )
        brent = next((x for x in self.deltas if x.asset == "brent"), None)
        if brent is not None:
            lines.append(
                f"  μ brent ${brent.old_mu:.0f} → ${brent.new_mu:.0f} "
                f"({brent.delta:+.0f}, {brent.pct:.0%})"
            )
        for x in self.deltas:
            if x.asset == "brent":
                continue
            lines.append(f"    {x.asset}: ${x.old_mu:.0f} → ${x.new_mu:.0f} ({x.delta:+.0f})")
        g = self.guardrail
        if not g.ok:
            lines.append(f"  ⚠ GUARDRAIL: {'; '.join(g.violations)}")
        else:
            lines.append(
                f"  guardrails OK (max |Δμ| {g.max_observed_pct:.0%} ≤ cap {g.cap_pct:.0%}; "
                f"инвариант bear<base<bull сохранён)"
            )
        return "\n".join(lines)


__all__ = [
    "DRIVER_NAMES",
    "valid_states",
    "is_valid_state",
    "now_iso",
    "FlagSource",
    "DriverDetection",
    "CalibrationSnapshot",
    "AssetMuDelta",
    "GuardrailReport",
    "MuProposal",
]
