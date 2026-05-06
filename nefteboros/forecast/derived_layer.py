"""Derived forecast layer — преобразование Brent-прогноза в Urals/ESPO/Минфин-blend.

Strict-separation подход (см. ADR-0012):
  - Модели обучаются на наблюдаемых рядах (brent, wti, ...).
  - Российские нефтесорта получаются здесь, post-prediction:
      * Urals/ESPO — из Brent_pred − spread (per период)
      * Минфин-blend — piecewise линейная комбинация Urals/ESPO

CI расширение (convolution):
  σ_target² = σ_brent² + σ_spread²
  где σ_spread = (high - low) / √12 для uniform[low, high].

Для blend (linear combo):
  σ_blend² = w1² × σ_urals² + w2² × σ_espo²
  (Предположение независимости spread-uncertainty между urals и espo.
   Они получены из одного Brent, поэтому brent-вариация correlated, но
   spread-вариации — из разных строк schedule, можно считать independent.
   Conservative bound для практики PR1.)
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd

from nefteboros.forecast.data.derived import (
    MINFIN_FORMULA_EFFECTIVE_FROM,
    MINFIN_ESPO_WEIGHT,
    MINFIN_URALS_WEIGHT,
)
from nefteboros.forecast.data.spread_schedule import (
    find_period_for_date,
    get_spread_for_date,
)
from nefteboros.forecast.schema import (
    ConfidenceInterval,
    ForecastPoint,
    ForecastResult,
    Horizon,
    ModelMethod,
)


# Z-значения для расчёта σ из CI (предполагая нормальное распределение)
_Z_BY_LEVEL = {0.80: 1.282, 0.90: 1.645, 0.95: 1.960, 0.99: 2.576}


# =============================================================================
# Public API
# =============================================================================


def derive_urals_forecast(brent_fc: ForecastResult) -> ForecastResult:
    """Brent-прогноз → Urals-прогноз через spread."""
    return _apply_spread_to_forecast(
        base=brent_fc,
        target_asset="urals",
    )


def derive_espo_forecast(brent_fc: ForecastResult) -> ForecastResult:
    """Brent-прогноз → ESPO-прогноз через spread."""
    return _apply_spread_to_forecast(
        base=brent_fc,
        target_asset="espo",
    )


def derive_minfin_blend_forecast(
    urals_fc: ForecastResult,
    espo_fc: ForecastResult,
) -> ForecastResult:
    """Piecewise blend из Urals и ESPO прогнозов.

    До 2025-01-01: blend = urals.
    С 2025-01-01:  blend = 0.78 × urals + 0.22 × espo.

    Решение по piecewise — на основе target-date end-point (день, на который
    делается прогноз, не сегодняшняя дата). Логика: формула применяется к
    тому месяцу, к которому относится прогнозируемое значение.
    """
    if urals_fc.horizon != espo_fc.horizon:
        raise ValueError(
            f"horizon mismatch: urals={urals_fc.horizon}, espo={espo_fc.horizon}"
        )
    if urals_fc.method != espo_fc.method:
        raise ValueError(
            f"method mismatch: urals={urals_fc.method}, espo={espo_fc.method}"
        )

    if len(urals_fc.points) != len(espo_fc.points):
        raise ValueError(
            f"point count mismatch: urals={len(urals_fc.points)}, espo={len(espo_fc.points)}"
        )

    new_points: list[ForecastPoint] = []
    for u_pt, e_pt in zip(urals_fc.points, espo_fc.points):
        if u_pt.date != e_pt.date:
            raise ValueError(f"date mismatch: urals={u_pt.date}, espo={e_pt.date}")
        new_pt = _blend_point(u_pt, e_pt)
        new_points.append(new_pt)

    # blend "last observed" = piecewise(urals_last, espo_last)
    today = pd.Timestamp.now(tz="UTC").normalize()
    urals_last = urals_fc.metadata.get("data_last_value")
    espo_last = espo_fc.metadata.get("data_last_value")
    if urals_last is not None and espo_last is not None:
        if today >= MINFIN_FORMULA_EFFECTIVE_FROM:
            blend_last_value = MINFIN_URALS_WEIGHT * float(urals_last) + MINFIN_ESPO_WEIGHT * float(espo_last)
        else:
            blend_last_value = float(urals_last)
    else:
        blend_last_value = None

    return ForecastResult(
        asset="urals_minfin_blend",
        horizon=urals_fc.horizon,
        method=urals_fc.method,
        points=new_points,
        interpretation=urals_fc.interpretation,
        backtest_summary=None,
        metadata={
            **{k: v for k, v in urals_fc.metadata.items() if k not in {"derived_from", "data_last_value"}},
            "derived_from": "urals + espo (Минфин piecewise НДПИ-формула)",
            "minfin_formula_effective_from": MINFIN_FORMULA_EFFECTIVE_FROM.isoformat(),
            "minfin_urals_weight": MINFIN_URALS_WEIGHT,
            "minfin_espo_weight": MINFIN_ESPO_WEIGHT,
            "data_last_value": blend_last_value,
        },
    )


# =============================================================================
# Internal
# =============================================================================


def _apply_spread_to_forecast(
    *,
    base: ForecastResult,
    target_asset: str,
) -> ForecastResult:
    """Преобразовать ForecastResult, вычитая spread per точка."""
    new_points: list[ForecastPoint] = []
    spread_metadata: list[dict] = []

    for pt in base.points:
        target_ts = pd.Timestamp(pt.date)
        spread_low, spread_mid, spread_high = get_spread_for_date(target_ts, target_asset)
        period = find_period_for_date(target_ts)

        # Point: brent − spread_mid
        new_value = pt.value - spread_mid

        # σ_brent из ci_80 (более robust к heavy tails чем 95)
        sigma_brent = _ci_to_sigma(pt.ci_80, pt.value)
        # σ_spread: uniform на [low, high]
        spread_width = max(spread_high - spread_low, 1e-9)
        sigma_spread = spread_width / math.sqrt(12.0)
        # convolution
        sigma_total = math.sqrt(sigma_brent**2 + sigma_spread**2)

        new_ci_80 = _sigma_to_ci(new_value, sigma_total, level=0.80)
        new_ci_95 = _sigma_to_ci(new_value, sigma_total, level=0.95)

        new_points.append(
            ForecastPoint(
                date=pt.date,
                value=new_value,
                ci_80=new_ci_80,
                ci_95=new_ci_95,
            )
        )
        spread_metadata.append(
            {
                "target_date": str(target_ts.date()),
                "spread_period": period.name,
                "spread_low": spread_low,
                "spread_mid": spread_mid,
                "spread_high": spread_high,
                "spread_source": period.source,
            }
        )

    # derived "last observed" = brent_last_value − spread_today (для cosmetic display)
    derived_last_value = base.metadata.get("data_last_value")
    today = pd.Timestamp.now(tz="UTC").normalize()
    if derived_last_value is not None:
        try:
            _, today_spread_mid, _ = get_spread_for_date(today, target_asset)
            derived_last_value = float(derived_last_value) - today_spread_mid
        except Exception:
            pass  # cap_phase mismatch — оставляем base value

    return ForecastResult(
        asset=target_asset,
        horizon=base.horizon,
        method=base.method,
        points=new_points,
        interpretation=base.interpretation,
        backtest_summary=None,  # derived не имеет собственного бектеста
        metadata={
            **base.metadata,
            "derived_from": "brent",
            "spread_per_point": spread_metadata,
            # Перезаписываем data_last_value на derived "as of today"
            "data_last_value_brent": base.metadata.get("data_last_value"),
            "data_last_value": derived_last_value,
        },
    )


def _blend_point(u_pt: ForecastPoint, e_pt: ForecastPoint) -> ForecastPoint:
    """Piecewise blend двух ForecastPoint по дате."""
    target_date = pd.Timestamp(u_pt.date)
    formula_active = target_date >= MINFIN_FORMULA_EFFECTIVE_FROM

    if formula_active:
        w_u, w_e = MINFIN_URALS_WEIGHT, MINFIN_ESPO_WEIGHT
    else:
        # До 2025-01: blend = urals
        w_u, w_e = 1.0, 0.0

    blend_value = w_u * u_pt.value + w_e * e_pt.value

    # σ_blend² = w_u² × σ_u² + w_e² × σ_e²
    sigma_u = _ci_to_sigma(u_pt.ci_80, u_pt.value)
    sigma_e = _ci_to_sigma(e_pt.ci_80, e_pt.value) if w_e > 0 else 0.0
    sigma_blend = math.sqrt((w_u * sigma_u) ** 2 + (w_e * sigma_e) ** 2)

    return ForecastPoint(
        date=u_pt.date,
        value=blend_value,
        ci_80=_sigma_to_ci(blend_value, sigma_blend, level=0.80),
        ci_95=_sigma_to_ci(blend_value, sigma_blend, level=0.95),
    )


def _ci_to_sigma(ci: ConfidenceInterval, mean: float) -> float:
    """Из CI извлечь σ предполагая нормальное распределение."""
    z = _Z_BY_LEVEL.get(ci.level)
    if z is None:
        raise ValueError(f"unsupported CI level={ci.level}; supported {sorted(_Z_BY_LEVEL)}")
    width = ci.high - ci.low
    if width <= 0:
        return 0.0
    return width / (2.0 * z)


def _sigma_to_ci(mean: float, sigma: float, *, level: float) -> ConfidenceInterval:
    z = _Z_BY_LEVEL[level]
    return ConfidenceInterval(level=level, low=mean - z * sigma, high=mean + z * sigma)


__all__ = [
    "derive_urals_forecast",
    "derive_espo_forecast",
    "derive_minfin_blend_forecast",
]
