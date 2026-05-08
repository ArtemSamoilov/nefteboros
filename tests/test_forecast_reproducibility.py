"""A3 — Reproducibility тесты для forecast и forecast_spread (OU regime, ADR-0024).

Покрывает:
- Unit (без сетки) — OU computation детерминирован, direction sanity, mean reversion physics.
- Network (`pytest -m network`) — full pipeline forecast() и forecast_spread()
  выдают bit-identical результат на двух последовательных вызовах.

См. ADR-0024 §A3, scenarios.FORECAST_RANDOM_STATE.
"""

from __future__ import annotations

import math

import pytest


# =============================================================================
# Unit tests — без сетки
# =============================================================================


class TestOUComputation:
    """compute_ou_forecast — детерминирован и физически корректен."""

    def test_ou_forecast_idempotent(self):
        """Same input → same output (детерминированность OU)."""
        from nefteboros.forecast.scenarios import (
            ASSET_PARAMS,
            compute_ou_forecast,
        )

        for asset in ("brent", "wti", "ttf", "gazp"):
            for scenario in ("bear", "base", "bull"):
                params = ASSET_PARAMS[asset][scenario]
                spot = 100.0
                f1 = compute_ou_forecast(spot, params, 6)
                f2 = compute_ou_forecast(spot, params, 6)
                assert f1.mid == f2.mid
                assert f1.ci_80_low == f2.ci_80_low
                assert f1.ci_80_high == f2.ci_80_high

    def test_ou_direction_sanity_oil(self):
        """Для нефти: bear < base < bull mid на всех horizons."""
        from nefteboros.forecast.scenarios import (
            ASSET_PARAMS,
            compute_ou_forecast,
        )

        for asset in ("brent", "wti", "urals", "espo", "urals_minfin_blend"):
            spot = ASSET_PARAMS[asset]["base"].mu_0  # roughly current spot
            for h in (1, 3, 6, 12):
                f_bear = compute_ou_forecast(spot, ASSET_PARAMS[asset]["bear"], h)
                f_base = compute_ou_forecast(spot, ASSET_PARAMS[asset]["base"], h)
                f_bull = compute_ou_forecast(spot, ASSET_PARAMS[asset]["bull"], h)
                assert f_bear.mid < f_base.mid < f_bull.mid, (
                    f"{asset} {h}m: bear={f_bear.mid:.1f} base={f_base.mid:.1f} bull={f_bull.mid:.1f}"
                )

    def test_ou_inverted_bull_for_moex(self):
        """MOEX: bull mid < base < bear (escalation hurts equity)."""
        from nefteboros.forecast.scenarios import (
            ASSET_PARAMS,
            compute_ou_forecast,
        )

        for asset in ("moexog", "gazp", "nvtk"):
            spot = ASSET_PARAMS[asset]["base"].mu_0
            for h in (3, 6, 12):
                f_bear = compute_ou_forecast(spot, ASSET_PARAMS[asset]["bear"], h)
                f_base = compute_ou_forecast(spot, ASSET_PARAMS[asset]["base"], h)
                f_bull = compute_ou_forecast(spot, ASSET_PARAMS[asset]["bull"], h)
                assert f_bull.mid < f_base.mid < f_bear.mid, (
                    f"{asset} {h}m INVERTED: "
                    f"bull={f_bull.mid:.1f} base={f_base.mid:.1f} bear={f_bear.mid:.1f}"
                )

    def test_ou_mean_reversion_bounded_ci(self):
        """OU CI **bounded** на длинных horizons (vs random walk √h расходится).

        Сравниваем width CI на 12m vs 6m: должна быть не более чем ×1.5
        (vs random walk где было бы √2 ≈ 1.41).
        """
        from nefteboros.forecast.scenarios import (
            ASSET_PARAMS,
            compute_ou_forecast,
        )

        for asset in ("brent", "wti"):
            for scenario in ("bear", "base", "bull"):
                params = ASSET_PARAMS[asset][scenario]
                spot = ASSET_PARAMS[asset]["base"].mu_0
                f6 = compute_ou_forecast(spot, params, 6)
                f12 = compute_ou_forecast(spot, params, 12)
                width_6 = f6.ci_80_high - f6.ci_80_low
                width_12 = f12.ci_80_high - f12.ci_80_low
                ratio = width_12 / width_6
                # На 12m CI bounded, ratio < 1.4 (vs random walk √2≈1.41)
                assert ratio < 1.4, (
                    f"{asset} {scenario}: width 6m={width_6:.1f} 12m={width_12:.1f} "
                    f"ratio={ratio:.2f} — CI не bounded (mean reversion broken)"
                )

    def test_inflation_drift_lifts_mid_over_time(self):
        """μ(t) дрейфует вверх с инфляцией → mid base scenario на 12m > 1m."""
        from nefteboros.forecast.scenarios import (
            ASSET_PARAMS,
            compute_ou_forecast,
        )

        # На base scenario (где driver shifts = 0 по построению), mid должен расти
        # за счёт inflation drift только.
        for asset in ("brent", "ttf", "moexog"):
            params = ASSET_PARAMS[asset]["base"]
            spot = params.mu_0  # spot ≈ μ_0 (нет shock anchor)
            f_1m = compute_ou_forecast(spot, params, 1)
            f_12m = compute_ou_forecast(spot, params, 12)
            assert f_12m.mid > f_1m.mid, (
                f"{asset}: 12m mid {f_12m.mid:.2f} should be > 1m mid {f_1m.mid:.2f} "
                f"(inflation drift)"
            )


class TestScenarioParsing:
    def test_parse_scenario_idempotent(self):
        from nefteboros.forecast.scenarios import parse_scenario

        for raw in (None, "base", "bear", "bull"):
            p1 = parse_scenario(raw)
            p2 = parse_scenario(raw)
            assert p1 == p2

    def test_parse_scenario_invalid(self):
        from nefteboros.forecast.scenarios import parse_scenario

        with pytest.raises(ValueError, match="Unknown scenario"):
            parse_scenario("invalid")

    def test_seed_helper_idempotent(self):
        import numpy as np

        from nefteboros.forecast.api import _seed_for_reproducibility

        _seed_for_reproducibility()
        a = np.random.rand(5)
        _seed_for_reproducibility()
        b = np.random.rand(5)
        assert (a == b).all()


# =============================================================================
# Network tests — full forecast pipeline
# =============================================================================


@pytest.mark.network
class TestForecastNetworkDeterminism:
    """forecast() и forecast_spread() — детерминированы на двух одинаковых вызовах."""

    def test_forecast_brent_3m_base_deterministic(self):
        from nefteboros.forecast import forecast

        r1 = forecast("brent", "3m", scenario="base", history_years=2.0)
        r2 = forecast("brent", "3m", scenario="base", history_years=2.0)

        assert r1.end_point.value == r2.end_point.value
        assert r1.end_point.ci_80.low == r2.end_point.ci_80.low
        assert r1.end_point.ci_80.high == r2.end_point.ci_80.high

    def test_forecast_brent_3m_bear_deterministic(self):
        from nefteboros.forecast import forecast

        r1 = forecast("brent", "3m", scenario="bear", history_years=2.0)
        r2 = forecast("brent", "3m", scenario="bear", history_years=2.0)
        assert r1.end_point.value == r2.end_point.value

    def test_forecast_brent_3m_bull_deterministic(self):
        from nefteboros.forecast import forecast

        r1 = forecast("brent", "3m", scenario="bull", history_years=2.0)
        r2 = forecast("brent", "3m", scenario="bull", history_years=2.0)
        assert r1.end_point.value == r2.end_point.value

    def test_forecast_spread_brent_wti_deterministic(self):
        from nefteboros.forecast import forecast_spread

        r1 = forecast_spread("brent", "wti", "3m", history_years=2.0)
        r2 = forecast_spread("brent", "wti", "3m", history_years=2.0)

        for s in ("bear", "base", "bull"):
            assert r1.per_scenario[s].spread_value == r2.per_scenario[s].spread_value

    def test_forecast_spread_brent_urals_deterministic(self):
        """brent-urals — schedule-anchored OU, deterministic."""
        from nefteboros.forecast import forecast_spread

        r1 = forecast_spread("brent", "urals", "3m")
        r2 = forecast_spread("brent", "urals", "3m")

        for s in ("bear", "base", "bull"):
            assert r1.per_scenario[s].spread_value == r2.per_scenario[s].spread_value

    def test_forecast_direction_sanity_full(self):
        """End-to-end: bear < base < bull в реальном выводе forecast()."""
        from nefteboros.forecast import forecast

        bear = forecast("brent", "3m", scenario="bear", history_years=2.0)
        base = forecast("brent", "3m", scenario="base", history_years=2.0)
        bull = forecast("brent", "3m", scenario="bull", history_years=2.0)

        assert bear.end_point.value < base.end_point.value < bull.end_point.value
