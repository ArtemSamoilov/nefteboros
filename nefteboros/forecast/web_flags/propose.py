"""Approve-gate: предложение «новости → новая μ» и его применение (ADR-0028).

Полу-авто с подтверждением (принято в ТЗ): веб считает новую μ → показывает diff +
источники → применяется ТОЛЬКО после явного подтверждения. Единственный путь,
который меняет активный snapshot — `apply_proposal(..., confirm=True)`. Молчаливого
авто нет by design: `build_proposal` чист (ничего не пишет), `apply_proposal` без
`confirm=True` поднимает `ApprovalRequired`.

Почему не молча: μ — детерминированный вход прогноза цены для аналитика Г. Грефа;
автоматическая подмена калибровки по непроверенной новости недопустима. Человек
видит diff (μ старое→новое), причину (переход состояния), цитаты tier-1 и вердикт
guardrails — и решает.
"""

from __future__ import annotations

from typing import Optional

from nefteboros.forecast.web_flags import guardrails as gr
from nefteboros.forecast.web_flags.detect import FlagDetector
from nefteboros.forecast.web_flags.models import (
    CalibrationSnapshot,
    DriverDetection,
    MuProposal,
    now_iso,
)
from nefteboros.forecast.web_flags.snapshot import SnapshotStore


class ApprovalRequired(RuntimeError):
    """apply_proposal вызван без явного confirm=True (approve-gate)."""


class GuardrailBlocked(RuntimeError):
    """Предложение нарушает guardrails и применяется без force=True."""


def build_proposal(
    base: CalibrationSnapshot,
    detections: list[DriverDetection],
    *,
    cap_pct: float = gr.DEFAULT_CAP_PCT,
) -> MuProposal:
    """Собрать предложение из активного snapshot + подтверждённых переходов.

    Чистая функция: не трогает хранилище (тестируема без диска/сети).
    """
    changed = [d for d in detections if d.changed]

    new_states = dict(base.flag_states)
    new_sources = {k: list(v) for k, v in base.sources.items()}
    for d in changed:
        new_states[d.driver] = d.detected_state
        new_sources[d.driver] = [s for s in d.sources if s.state == d.detected_state]

    note = (
        "web-flags update: "
        + ", ".join(f"{d.driver} {d.prior_state}→{d.detected_state}" for d in changed)
        if changed
        else "web-flags: no confirmed transitions"
    )
    proposed = CalibrationSnapshot(
        as_of=now_iso(),
        flag_states=new_states,
        sources=new_sources,
        parent_version=base.version,
        note=note,
    )
    deltas = gr.compute_deltas(base, proposed)
    guardrail = gr.evaluate(base, proposed, deltas, cap_pct=cap_pct)
    return MuProposal(
        base=base,
        proposed=proposed,
        changed_drivers=changed,
        deltas=deltas,
        guardrail=guardrail,
    )


def propose_from_web(
    store: SnapshotStore,
    detector: Optional[FlagDetector] = None,
    *,
    cap_pct: float = gr.DEFAULT_CAP_PCT,
) -> MuProposal:
    """End-to-end предложение: активный snapshot → детекция всех драйверов → proposal.

    Логирует событие `propose` в diff-лог. Ничего не применяет.
    """
    detector = detector or FlagDetector()
    base = store.load_active()
    detections = detector.detect_all(base.flag_states)
    proposal = build_proposal(base, detections, cap_pct=cap_pct)
    store.log_event(
        "propose",
        {
            "base_version": base.version,
            "changed": [
                {"driver": d.driver, "from": d.prior_state, "to": d.detected_state}
                for d in proposal.changed_drivers
            ],
            "deltas": {x.asset: round(x.delta, 3) for x in proposal.deltas},
            "guardrail_ok": proposal.guardrail.ok,
            "guardrail_violations": proposal.guardrail.violations,
        },
    )
    return proposal


def apply_proposal(
    store: SnapshotStore,
    proposal: MuProposal,
    *,
    confirm: bool,
    force: bool = False,
) -> CalibrationSnapshot:
    """Применить предложение к активному snapshot — ТОЛЬКО при confirm=True.

    Raises:
        ApprovalRequired: confirm != True (никогда не применяем молча).
        GuardrailBlocked: guardrails не пройдены и force != True.
    """
    if not confirm:
        store.log_event(
            "reject",
            {"base_version": proposal.base.version, "reason": "not confirmed (approve-gate)"},
        )
        raise ApprovalRequired(
            "approve-gate: обновление калибровки требует явного подтверждения (confirm=True)."
        )

    if not proposal.has_changes:
        store.log_event("noop", {"base_version": proposal.base.version})
        return proposal.base

    if not proposal.guardrail.ok and not force:
        store.log_event(
            "blocked",
            {
                "base_version": proposal.base.version,
                "violations": proposal.guardrail.violations,
            },
        )
        raise GuardrailBlocked(
            "guardrails не пройдены: " + "; ".join(proposal.guardrail.violations) +
            " (примените с force=True для override)."
        )

    return store.commit(proposal.proposed, set_active=True, log_action="apply")


def active_forecast(
    asset: str,
    horizon: str,
    *,
    store: Optional[SnapshotStore] = None,
    scenario: Optional[str] = None,
    **kwargs,
):
    """forecast() с μ из АКТИВНОГО snapshot (reproducibility).

    Читает flag_states активного snapshot и передаёт в forecast() явно — дефолт
    forecast() (flag_states=None) НЕ меняется. Веб тут не дёргается: используется
    уже approved snapshot. Для не-нефти snapshot не применяется (forecast вернёт
    обычный путь через scenario).
    """
    from nefteboros.forecast.api import forecast
    from nefteboros.forecast.scenarios import OIL_ASSETS

    store = store or SnapshotStore()
    active = store.load_active()
    flags = active.flag_states if asset in OIL_ASSETS else None
    return forecast(asset, horizon, scenario=scenario, flag_states=flags, **kwargs)


__all__ = [
    "ApprovalRequired",
    "GuardrailBlocked",
    "build_proposal",
    "propose_from_web",
    "apply_proposal",
    "active_forecast",
]
