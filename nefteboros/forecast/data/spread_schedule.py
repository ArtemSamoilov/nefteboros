"""Hardcoded Brent-Urals / Brent-ESPO spread schedule.

См. ADR-0012 §«Архитектура per asset» — strict-separation подход.

Periodization намеренно coarse: 4 режима за 5 лет — это *режимные* states рынка
(sanctions cap, shadow fleet etc.), не daily-волатильность спреда. При
наблюдаемой систематической ошибке прогноза Urals в эксперименте — пересмотреть
периоды (ввести 5-й, сместить границы, или взять externally-extracted spread из
Bruegel WP).

Каждый период — (low, mid, high) discount в USD/bbl. Positive = russian crude
ниже Brent. Negative = russian crude выше Brent (Asian premium для ESPO до 2022).

Использование:
    from nefteboros.forecast.data.spread_schedule import get_spread_for_date

    low, mid, high = get_spread_for_date(pd.Timestamp("2024-06-01"), "urals")
    # → (15.0, 22.0, 28.0)

CI расширения для derived-актива:
    σ_spread = (high - low) / sqrt(12)         # uniform на [low, high]
    σ_total² = σ_brent² + σ_spread²
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

import pandas as pd


SpreadComponent = Literal["low", "mid", "high"]
RussianGrade = Literal["urals", "espo"]


@dataclass(frozen=True)
class SpreadPeriod:
    """Один режим Brent-Urals/ESPO спреда (discount в USD/bbl).

    `urals_*` и `espo_*` — три точки распределения discount: low/mid/high.
    Распределение трактуется как uniform на [low, high] для расчёта дисперсии
    в CI-convolution.
    """

    name: str
    start: date           # inclusive
    end: Optional[date]   # exclusive; None = до настоящего
    urals_low: float
    urals_mid: float
    urals_high: float
    espo_low: float
    espo_mid: float
    espo_high: float
    source: str
    notes: str = ""

    def __contains__(self, d: date) -> bool:
        if d < self.start:
            return False
        if self.end is not None and d >= self.end:
            return False
        return True


# =============================================================================
# Schedule
# =============================================================================
# Числа: middle = published среднеквартальное значение из source; low/high —
# наблюдаемые квартальные min/max (приблизительно), отражают режимную
# волатильность. Это не "погрешность измерения", это "spread мог быть в этом
# диапазоне в любой день периода".

SPREAD_SCHEDULE: list[SpreadPeriod] = [
    SpreadPeriod(
        name="pre_war",
        start=date(2021, 1, 1),
        end=date(2022, 2, 24),
        urals_low=0.5,   urals_mid=1.5,   urals_high=2.5,
        # ESPO premium до санкций — Asian buyers платят выше Brent
        espo_low=-3.0,   espo_mid=-1.5,   espo_high=0.0,
        source="Bruegel WP 32/2025 §2.1; EIA Russia country overview 2022",
        notes=(
            "Pre-Ukraine war: Urals торговался с минимальным дисконтом ~$1-2; "
            "ESPO часто с премией к Brent из-за Asian (China/Japan/Korea) buying."
        ),
    ),
    SpreadPeriod(
        name="war_shock",
        start=date(2022, 2, 24),
        end=date(2022, 12, 31),
        urals_low=20.0,  urals_mid=30.0,  urals_high=40.0,
        espo_low=2.0,    espo_mid=7.0,    espo_high=12.0,
        source="Bruegel WP 32/2025 §2.2; S&P Platts Russia oil blog Nov 2022",
        notes=(
            "Russia-Ukraine war shock: Urals discount expanded с $3 до $35 "
            "за 6 недель (Feb-Apr 2022); ESPO держался лучше за счёт Asian buyers."
        ),
    ),
    SpreadPeriod(
        name="cap_phase_1",
        start=date(2023, 1, 1),
        end=date(2025, 8, 31),
        urals_low=15.0,  urals_mid=22.0,  urals_high=28.0,
        espo_low=2.0,    espo_mid=5.0,    espo_high=8.0,
        source="Bruegel WP 32/2025 §3; Минэк бюджет РФ 2024-2026; КонсультантПлюс",
        notes=(
            "G7 price cap $60: Urals discount стабилизирован ~$20-25; "
            "shadow fleet absorbs logistics premium; "
            "ESPO discount меньше (Asian направления вне cap)."
        ),
    ),
    SpreadPeriod(
        name="cap_phase_2",
        start=date(2025, 9, 1),
        end=None,
        urals_low=12.0,  urals_mid=17.0,  urals_high=22.0,
        espo_low=3.0,    espo_mid=6.0,    espo_high=9.0,
        source=(
            "Bruegel WP 32/2025 §4 (cap снижен до $47.60 09.2025); "
            "Минфин $59 в проекте бюджета 2026"
        ),
        notes=(
            "G7 reduced cap to $47.60 in Sep 2025; Urals discount slightly "
            "compressed as cap closer to spot price level."
        ),
    ),
]


# =============================================================================
# Public API
# =============================================================================


def find_period_for_date(target: pd.Timestamp) -> SpreadPeriod:
    """Найти SpreadPeriod, в который попадает дата. Бросает ValueError если outside."""
    target_d = pd.Timestamp(target).date()
    for period in SPREAD_SCHEDULE:
        if target_d in period:
            return period
    raise ValueError(
        f"date {target_d} outside spread schedule. "
        f"Range: {SPREAD_SCHEDULE[0].start} .. now. "
        f"Если новый период (cap_phase_3 etc.) — обнови SPREAD_SCHEDULE."
    )


def get_spread_for_date(
    target: pd.Timestamp,
    asset_id: RussianGrade,
) -> tuple[float, float, float]:
    """Вернуть (low, mid, high) discount в USD/bbl для актива на дату.

    Discount = Brent - asset_price; positive = asset ниже Brent.
    """
    if asset_id not in ("urals", "espo"):
        raise ValueError(
            f"spread_schedule покрывает только urals/espo, got {asset_id!r}"
        )
    period = find_period_for_date(target)
    if asset_id == "urals":
        return (period.urals_low, period.urals_mid, period.urals_high)
    return (period.espo_low, period.espo_mid, period.espo_high)


def get_spread_series(
    dates: pd.DatetimeIndex,
    asset_id: RussianGrade,
    *,
    component: SpreadComponent = "mid",
) -> pd.Series:
    """Spread-серия per дату из индекса (low|mid|high компонент)."""
    if component not in ("low", "mid", "high"):
        raise ValueError(f"component must be low/mid/high, got {component!r}")
    idx_map = {"low": 0, "mid": 1, "high": 2}[component]
    values = [get_spread_for_date(d, asset_id)[idx_map] for d in dates]
    return pd.Series(
        values,
        index=dates,
        name=f"{asset_id}_spread_{component}",
        dtype=float,
    )


def get_period_label_series(dates: pd.DatetimeIndex) -> pd.Series:
    """Метки режимов per дату — для regime-segmented бектеста и эксперимента."""
    labels = [find_period_for_date(d).name for d in dates]
    return pd.Series(labels, index=dates, name="spread_period", dtype="object")


__all__ = [
    "SpreadPeriod",
    "SpreadComponent",
    "RussianGrade",
    "SPREAD_SCHEDULE",
    "find_period_for_date",
    "get_spread_for_date",
    "get_spread_series",
    "get_period_label_series",
]
