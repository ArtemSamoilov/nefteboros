"""A7 sensitivity test — sigma_dollar anchor (σ×spot vs σ×mid vs geometric mean).

См. ADR-0024 §A7. На extreme bear/bull сценариях, где mid дрейфует далеко от
spot, выбор anchor для sigma_dollar = σ × X влияет на ширину CI значимо.

Test замеряет разницу в ширине CI на представительных сценариях. Резулатат
зашит как regression check — если изменим anchor logic, тест сломается.
"""

from __future__ import annotations

import math

import pytest


# Re-implement OU formula with each variant — для замера, не для production
def _ou_with_anchor(
    spot: float, mu_0: float, theta: float, sigma: float, infl: float,
    horizon_months: int, anchor: str,
) -> tuple[float, float]:
    """Return (mid, ci_80_width) для variant anchor in {spot, mid, geomean}."""
    t = horizon_months / 12.0
    mu_t = mu_0 * (1 + infl * t)
    mid = mu_t + (spot - mu_0) * math.exp(-theta * t)

    if anchor == "spot":
        sigma_dollar = sigma * spot
    elif anchor == "mid":
        sigma_dollar = sigma * abs(mid)
    elif anchor == "geomean":
        sigma_dollar = sigma * math.sqrt(spot * abs(mid))
    else:
        raise ValueError(anchor)

    var = (sigma_dollar ** 2 / (2 * theta)) * (1 - math.exp(-2 * theta * t))
    sd = math.sqrt(var)
    width = 2 * 1.282 * sd
    return mid, width


class TestSigmaAnchorSensitivity:
    """Замер чувствительности CI ширины к выбору anchor."""

    def test_extreme_bear_anchor_diff_significant(self):
        """Brent bear 12m: mid дрейфует к $70 от spot $100 — разница ширины 25-30%."""
        # Brent bear params (см. scenarios.ASSET_PARAMS)
        spot, mu_0, theta, sigma, infl = 100.0, 70.0, 3.0, 0.20, 0.05
        m_spot, w_spot = _ou_with_anchor(spot, mu_0, theta, sigma, infl, 12, "spot")
        m_mid, w_mid = _ou_with_anchor(spot, mu_0, theta, sigma, infl, 12, "mid")
        # Mid should be same (mid не зависит от σ)
        assert abs(m_spot - m_mid) < 0.01
        # Width should be significantly narrower with σ × mid
        ratio = w_mid / w_spot
        assert 0.7 < ratio < 0.78, (
            f"bear extreme: w_mid/w_spot = {ratio:.3f}, expected ~0.73 "
            f"(mid={m_mid:.2f}, spot={spot})"
        )

    def test_extreme_bull_anchor_diff_significant(self):
        """Brent bull 12m: mid дрейфует к $120 от spot $100 — разница ширины 18-22%."""
        spot, mu_0, theta, sigma, infl = 100.0, 120.0, 1.5, 0.30, 0.05
        m_spot, w_spot = _ou_with_anchor(spot, mu_0, theta, sigma, infl, 12, "spot")
        m_mid, w_mid = _ou_with_anchor(spot, mu_0, theta, sigma, infl, 12, "mid")
        ratio = w_mid / w_spot
        # mid > spot → wider CI on σ × mid
        assert 1.18 < ratio < 1.25, (
            f"bull extreme: w_mid/w_spot = {ratio:.3f}, expected ~1.21 "
            f"(mid={m_mid:.2f}, spot={spot})"
        )

    def test_base_anchor_diff_negligible(self):
        """Brent base 12m: mid близок к spot — разница ширины < 5%."""
        spot, mu_0, theta, sigma, infl = 100.0, 98.0, 2.0, 0.25, 0.05
        m_spot, w_spot = _ou_with_anchor(spot, mu_0, theta, sigma, infl, 12, "spot")
        m_mid, w_mid = _ou_with_anchor(spot, mu_0, theta, sigma, infl, 12, "mid")
        ratio = w_mid / w_spot
        assert 0.95 < ratio < 1.10, (
            f"base ~spot: w_mid/w_spot = {ratio:.3f}, expected close to 1.0"
        )

    def test_mid_independent_of_sigma_choice(self):
        """Critical invariant: mid не зависит от sigma в OU — формула не recursive."""
        spot, mu_0, theta, infl = 100.0, 70.0, 3.0, 0.05
        # Меняем σ — mid должен оставаться тем же
        m_a = _ou_with_anchor(spot, mu_0, theta, 0.10, infl, 12, "mid")[0]
        m_b = _ou_with_anchor(spot, mu_0, theta, 0.50, infl, 12, "mid")[0]
        assert abs(m_a - m_b) < 0.0001

    def test_production_uses_mid_anchor(self):
        """Verify production compute_ou_forecast uses σ × mid (not spot)."""
        from nefteboros.forecast.scenarios import OUParams, compute_ou_forecast

        # Brent bear extreme
        params = OUParams(mu_0=70.0, theta=3.0, sigma=0.20, inflation=0.05)
        prod_result = compute_ou_forecast(spot=100.0, params=params, horizon_months=12)
        prod_width = prod_result.ci_80_high - prod_result.ci_80_low

        # Reference: σ × mid
        m_mid, w_mid_ref = _ou_with_anchor(100.0, 70.0, 3.0, 0.20, 0.05, 12, "mid")
        # Allow small floating-point drift
        assert abs(prod_width - w_mid_ref) < 0.05, (
            f"prod width {prod_width:.3f} vs σ×mid reference {w_mid_ref:.3f}"
        )
