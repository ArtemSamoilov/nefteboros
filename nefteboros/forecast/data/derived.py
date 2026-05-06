"""Derived series: Urals/ESPO/Минфин-blend как историческая реконструкция.

ADR-0012 strict-separation:
  - Модели обучаются на наблюдаемых рядах (brent, wti, hh, ttf, moexog, gazp, nvtk).
  - Urals/ESPO/blend как **исторические series** для visualization, eval-baseline,
    sanity-check spread schedule — получаются здесь.
  - **Forecast** для Urals/ESPO/blend — отдельно в `forecast.derived_layer`
    (применяется поверх Brent-forecast с CI расширением).

Использование:
    from nefteboros.forecast.data.yf import fetch_yfinance
    from nefteboros.forecast.data.derived import (
        derive_urals_history, derive_espo_history, derive_minfin_blend_history,
    )

    brent = fetch_yfinance("brent", since=...)
    urals = derive_urals_history(brent)        # mid-spread per period
    espo = derive_espo_history(brent)
    blend = derive_minfin_blend_history(urals, espo)  # piecewise по 2025-01-01
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd

from nefteboros.forecast.data.spread_schedule import (
    SpreadComponent,
    get_spread_series,
)


# Минфин blend: формула 0.78×Urals + 0.22×ESPO вступила в силу с 2025-01-01.
# До этого база НДПИ считалась только по Urals (1.0×Urals).
MINFIN_FORMULA_EFFECTIVE_FROM = pd.Timestamp("2025-01-01", tz="UTC")
MINFIN_URALS_WEIGHT = 0.78
MINFIN_ESPO_WEIGHT = 0.22


# =============================================================================
# Public API
# =============================================================================


def derive_urals_history(
    brent: pd.Series,
    *,
    component: SpreadComponent = "mid",
) -> pd.Series:
    """Историческая Urals = Brent - spread_<component> per period."""
    spread = get_spread_series(brent.index, "urals", component=component)
    out = brent - spread
    out.name = "urals_derived"
    return out


def derive_espo_history(
    brent: pd.Series,
    *,
    component: SpreadComponent = "mid",
) -> pd.Series:
    """Историческая ESPO = Brent - spread_<component> per period.

    Note: spread может быть отрицательным (ESPO выше Brent — Asian premium до 2022).
    """
    spread = get_spread_series(brent.index, "espo", component=component)
    out = brent - spread
    out.name = "espo_derived"
    return out


def derive_minfin_blend_history(
    urals: pd.Series,
    espo: pd.Series,
) -> pd.Series:
    """Минфин-blend по piecewise-формуле.

    До 2025-01-01: blend = Urals (1.0).
    С 2025-01-01:  blend = 0.78 × Urals + 0.22 × ESPO.

    Args:
        urals, espo: ряды одной длины и индекса (UTC).
    """
    if not urals.index.equals(espo.index):
        # выравниваем по common dates
        common = urals.index.intersection(espo.index)
        urals = urals.loc[common]
        espo = espo.loc[common]

    is_new_formula = urals.index >= MINFIN_FORMULA_EFFECTIVE_FROM

    blend = pd.Series(index=urals.index, dtype=float, name="urals_minfin_blend_derived")
    # До формулы — просто Urals
    blend[~is_new_formula] = urals[~is_new_formula]
    # После формулы — взвешенная сумма
    blend[is_new_formula] = (
        MINFIN_URALS_WEIGHT * urals[is_new_formula]
        + MINFIN_ESPO_WEIGHT * espo[is_new_formula]
    )
    return blend


__all__ = [
    "derive_urals_history",
    "derive_espo_history",
    "derive_minfin_blend_history",
    "MINFIN_FORMULA_EFFECTIVE_FROM",
    "MINFIN_URALS_WEIGHT",
    "MINFIN_ESPO_WEIGHT",
]
