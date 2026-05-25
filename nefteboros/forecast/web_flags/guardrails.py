"""Guardrails перехода snapshot→snapshot (ADR-0028).

Три проверки на каждое предложенное обновление:
  1. **Δμ-cap** — |Δμ|/μ_old по каждому нефтяному активу ≤ `cap_pct`. Это ГЕЙТ,
     не клэмп: μ не хранится (выводится из flag_states), её нельзя «подрезать» —
     поэтому слишком большой скачок БЛОКИРУЕТ авто-применение (нужен явный override
     при approve), а не молча обрезается.
  2. **Инвариант bear<base<bull** — монотонность μ-поверхности (`compute_mu_from_flags`
     на трёх пресетах). Регресс-страж: ловит поломку калибровки этапа 1/изменений
     ADR-0028. Свойство цепочки, не зависит от конкретного snapshot.
  3. **Направление** — Δ supply-баланса и Δμ должны иметь противоположный знак
     (больше профицита ⇒ ниже μ). Ловит инверсию знака в цепочке.

Полный diff-лог обновлений — в `snapshot.SnapshotStore.log_event` (jsonl).
"""

from __future__ import annotations

from collections.abc import Sequence

from nefteboros.forecast.scenarios import (
    FLAG_PRESETS,
    OIL_ASSETS,
    compute_mu_from_flags,
    supply_balance_from_flags,
)
from nefteboros.forecast.web_flags.models import (
    AssetMuDelta,
    CalibrationSnapshot,
    GuardrailReport,
)

# Максимальный |Δμ| за одно обновление. $98→$70 (полный разворот Hormuz+Iran за
# один шаг) ≈ 29%, одиночный Hormuz reopen ≈ 27% — должны проходить; >35% за один
# апдейт подозрительно (несколько крупных событий разом) ⇒ требует override.
DEFAULT_CAP_PCT: float = 0.35


def compute_deltas(
    base: CalibrationSnapshot,
    proposed: CalibrationSnapshot,
) -> list[AssetMuDelta]:
    """Δμ по каждому нефтяному активу между base и proposed snapshot."""
    out: list[AssetMuDelta] = []
    for asset in sorted(OIL_ASSETS):
        out.append(
            AssetMuDelta(
                asset=asset,
                old_mu=base.mu(asset),
                new_mu=proposed.mu(asset),
            )
        )
    return out


def check_delta_cap(
    deltas: Sequence[AssetMuDelta],
    cap_pct: float = DEFAULT_CAP_PCT,
) -> tuple[bool, float, list[str]]:
    """True если все |Δμ|/μ_old ≤ cap_pct."""
    violations: list[str] = []
    max_pct = 0.0
    for d in deltas:
        max_pct = max(max_pct, d.pct)
        if d.pct > cap_pct:
            violations.append(
                f"{d.asset}: |Δμ| {d.pct:.0%} > cap {cap_pct:.0%} "
                f"(${d.old_mu:.0f}→${d.new_mu:.0f})"
            )
    return (not violations, max_pct, violations)


def check_invariant() -> tuple[bool, list[str]]:
    """Инвариант bear<base<bull на μ-поверхности (все нефтяные активы).

    Свойство `compute_mu_from_flags` (этап 1 + ADR-0028 непрерывная поверхность):
    пресеты упорядочены. Ловит поломку калибровки.
    """
    violations: list[str] = []
    for asset in sorted(OIL_ASSETS):
        bear = compute_mu_from_flags(asset, FLAG_PRESETS["bear"])
        base = compute_mu_from_flags(asset, FLAG_PRESETS["base"])
        bull = compute_mu_from_flags(asset, FLAG_PRESETS["bull"])
        if not (bear < base < bull):
            violations.append(
                f"{asset}: инвариант нарушен bear={bear:.1f} base={base:.1f} bull={bull:.1f}"
            )
    return (not violations, violations)


def check_direction(
    base: CalibrationSnapshot,
    proposed: CalibrationSnapshot,
) -> tuple[bool, list[str]]:
    """Δ supply-баланса и Δμ_brent должны быть противоположных знаков.

    Больше профицита (Δbalance>0) ⇒ ниже μ (Δμ<0). Нулевой Δ — ок.
    """
    d_balance = supply_balance_from_flags(proposed.flag_states) - supply_balance_from_flags(
        base.flag_states
    )
    d_mu = proposed.mu("brent") - base.mu("brent")
    if abs(d_balance) < 1e-9 or abs(d_mu) < 1e-9:
        return True, []
    if d_balance * d_mu > 0:  # одинаковый знак — инверсия
        return False, [
            f"Инверсия направления: Δbalance={d_balance:+.2f} mbpd и "
            f"Δμ_brent={d_mu:+.1f} одного знака (профицит должен снижать μ)."
        ]
    return True, []


def evaluate(
    base: CalibrationSnapshot,
    proposed: CalibrationSnapshot,
    deltas: Sequence[AssetMuDelta],
    *,
    cap_pct: float = DEFAULT_CAP_PCT,
) -> GuardrailReport:
    """Полный guardrail-отчёт перехода base→proposed."""
    cap_ok, max_pct, cap_violations = check_delta_cap(deltas, cap_pct)
    inv_ok, inv_violations = check_invariant()
    dir_ok, dir_violations = check_direction(base, proposed)

    return GuardrailReport(
        ok=cap_ok and inv_ok and dir_ok,
        cap_pct=cap_pct,
        max_observed_pct=max_pct,
        invariant_ok=inv_ok,
        cap_violations=cap_violations,
        invariant_violations=[*inv_violations, *dir_violations],
    )


__all__ = [
    "DEFAULT_CAP_PCT",
    "compute_deltas",
    "check_delta_cap",
    "check_invariant",
    "check_direction",
    "evaluate",
]
