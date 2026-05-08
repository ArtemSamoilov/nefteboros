"""A3 — Reproducibility тесты для forecast и forecast_spread.

Цель — гарантировать, что одинаковый вход → одинаковый выход в рамках одной
сессии. Покрывает:

- Unit (без сетки) — сценарная shift-логика и парсинг детерминированы.
- Network (`pytest -m network`) — полный pipeline forecast() и forecast_spread()
  выдают bit-identical результат на двух последовательных вызовах.

См. ADR-0023 §A3, scenarios.FORECAST_RANDOM_STATE.
"""

from __future__ import annotations

import pytest


# =============================================================================
# Unit tests — без сетки
# =============================================================================


class TestScenarioDeterminism:
    """compute_scenario_delta / parse_scenario / scenario_label — детерминированы.

    Это нужно потому что эти функции вызываются на каждом forecast(); если они
    зависят от global state (np.random) — два одинаковых вызова разойдутся.
    """

    def test_compute_scenario_delta_idempotent(self):
        from nefteboros.forecast.scenarios import (
            PRESET_SCENARIOS,
            compute_scenario_delta,
        )

        for asset in ("brent", "wti"):
            for scenario_name in ("base", "bear", "bull"):
                params = PRESET_SCENARIOS[scenario_name]
                d1 = compute_scenario_delta(params, asset)
                d2 = compute_scenario_delta(params, asset)
                assert d1.low == d2.low
                assert d1.mid == d2.mid
                assert d1.high == d2.high
                assert d1.driver_breakdown == d2.driver_breakdown

    def test_parse_scenario_idempotent(self):
        from nefteboros.forecast.scenarios import parse_scenario

        for raw in (None, "base", "bear", "bull"):
            p1 = parse_scenario(raw)
            p2 = parse_scenario(raw)
            assert p1 == p2

    def test_compute_base_anchor_idempotent(self):
        from nefteboros.forecast.scenarios import compute_base_anchor

        a1 = compute_base_anchor(raw_model_value=68.0, observed_spot=100.06)
        a2 = compute_base_anchor(raw_model_value=68.0, observed_spot=100.06)
        assert a1.anchor_shift == a2.anchor_shift

    def test_direction_sanity(self):
        """Структурный инвариант: bear < base < bull для Brent."""
        from nefteboros.forecast.scenarios import (
            PRESET_SCENARIOS,
            compute_scenario_delta,
        )

        for asset in ("brent", "wti"):
            d_bear = compute_scenario_delta(PRESET_SCENARIOS["bear"], asset)
            d_base = compute_scenario_delta(PRESET_SCENARIOS["base"], asset)
            d_bull = compute_scenario_delta(PRESET_SCENARIOS["bull"], asset)
            assert d_bear.mid < d_base.mid < d_bull.mid, (
                f"{asset}: bear={d_bear.mid} base={d_base.mid} bull={d_bull.mid} "
                f"(требуется bear < base < bull)"
            )

    def test_scenario_not_applicable_raises(self):
        """Газ и russian energy proxy — scenario не применим в v2.1."""
        from nefteboros.forecast.scenarios import (
            PRESET_SCENARIOS,
            compute_scenario_delta,
            is_scenario_applicable,
        )

        for asset in ("ttf", "henry_hub", "moexog", "gazp", "nvtk"):
            assert not is_scenario_applicable(asset)
            with pytest.raises(ValueError, match="scenario не применяется"):
                compute_scenario_delta(PRESET_SCENARIOS["base"], asset)

    def test_seed_helper_idempotent(self):
        """_seed_for_reproducibility() ставит deterministic state."""
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
    """forecast() и forecast_spread() — детерминированы на двух одинаковых вызовах.

    Использует use_cache=True по умолчанию — повторный вызов берёт данные из
    локального кеша (заполненного первым вызовом), что гарантирует одинаковость
    history_input. Если cache работает корректно — модель должна выдать тот же
    результат благодаря _seed_for_reproducibility().
    """

    def test_forecast_brent_3m_base_deterministic(self):
        from nefteboros.forecast import forecast

        r1 = forecast("brent", "3m", scenario="base", history_years=2.0)
        r2 = forecast("brent", "3m", scenario="base", history_years=2.0)

        assert r1.end_point.value == r2.end_point.value, (
            f"forecast brent base 3m не детерминирован: "
            f"{r1.end_point.value} != {r2.end_point.value}"
        )
        assert r1.end_point.ci_80.low == r2.end_point.ci_80.low
        assert r1.end_point.ci_80.high == r2.end_point.ci_80.high
        assert r1.metadata["base_anchor_shift"] == r2.metadata["base_anchor_shift"]

    def test_forecast_brent_3m_bear_deterministic(self):
        from nefteboros.forecast import forecast

        r1 = forecast("brent", "3m", scenario="bear", history_years=2.0)
        r2 = forecast("brent", "3m", scenario="bear", history_years=2.0)

        assert r1.end_point.value == r2.end_point.value
        assert r1.metadata["scenario_delta_mid"] == r2.metadata["scenario_delta_mid"]

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
            assert r1.per_scenario[s].spread_value == r2.per_scenario[s].spread_value, (
                f"forecast_spread(brent,wti) {s}: "
                f"{r1.per_scenario[s].spread_value} != {r2.per_scenario[s].spread_value}"
            )
            assert r1.per_scenario[s].ci_80.low == r2.per_scenario[s].ci_80.low

    def test_forecast_spread_brent_urals_deterministic(self):
        """brent-urals — schedule lookup, deterministic by definition (без сетки)."""
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

        assert bear.end_point.value < base.end_point.value < bull.end_point.value, (
            f"direction sanity failed: "
            f"bear={bear.end_point.value} base={base.end_point.value} bull={bull.end_point.value}"
        )
