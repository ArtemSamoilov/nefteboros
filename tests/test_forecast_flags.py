"""Flag-driven μ — детерминированная цепочка флаги→μ (ADR-0025).

Делает геополитические флаги РЕАЛЬНЫМ детерминированным входом μ (а не текстом).

Покрытие:
- Unit (без сети): регресс сходимости пресетов, реакция на hormuz, инвариант
  bear<base<bull, валидация, обратная совместимость.
- Network (`pytest -m network`): forecast() с flag_states end-to-end.

См. ADR-0025 §«Регресс сходимости» и §«Реакция на флаги».
"""

from __future__ import annotations

import pytest

OIL = ("brent", "wti", "urals", "espo", "urals_minfin_blend")

# Цепочка точна для bear/base, для bull аффинная карта даёт ≤$0.05 (round).
_CONVERGENCE_TOL = 0.1


# =============================================================================
# (а) Регресс сходимости — пресеты воспроизводят замороженные μ из ASSET_PARAMS
# =============================================================================


class TestFlagChainConvergence:
    def test_presets_reproduce_frozen_mu(self):
        """base/bear/bull наборы флагов → текущие μ из ASSET_PARAMS (все нефти)."""
        from nefteboros.forecast.scenarios import (
            ASSET_PARAMS,
            FLAG_PRESETS,
            compute_mu_from_flags,
        )

        for asset in OIL:
            for scen in ("base", "bear", "bull"):
                frozen = ASSET_PARAMS[asset][scen].mu_0
                chained = compute_mu_from_flags(asset, FLAG_PRESETS[scen])
                assert abs(chained - frozen) < _CONVERGENCE_TOL, (
                    f"{asset} {scen}: chain μ={chained:.3f} vs frozen {frozen} "
                    f"(diff {chained - frozen:+.3f})"
                )

    def test_bear_headline_chain_exact(self):
        """Headline-проверка ТЗ/ADR: brent bear = calm 89.2 − 1.6·$12 = $70.0 точно."""
        from nefteboros.forecast.scenarios import (
            CALM_BASELINE_BRENT,
            FLAG_PRESETS,
            KILIAN_USD_PER_MBPD,
            compute_mu_from_flags,
            supply_balance_from_flags,
        )

        s = supply_balance_from_flags(FLAG_PRESETS["bear"])
        assert s == pytest.approx(1.6)
        assert CALM_BASELINE_BRENT - KILIAN_USD_PER_MBPD * s == pytest.approx(70.0)
        assert compute_mu_from_flags("brent", FLAG_PRESETS["bear"]) == pytest.approx(70.0)

    def test_base_is_anchored_not_calm_baseline(self):
        """base (Σ=0) → замороженная μ_base (anchored), НЕ calm_baseline (особый случай)."""
        from nefteboros.forecast.scenarios import (
            ASSET_PARAMS,
            CALM_BASELINE_BRENT,
            FLAG_PRESETS,
            compute_mu_from_flags,
        )

        mu = compute_mu_from_flags("brent", FLAG_PRESETS["base"])
        assert mu == pytest.approx(ASSET_PARAMS["brent"]["base"].mu_0)  # 98, не 89.2
        assert mu != pytest.approx(CALM_BASELINE_BRENT)

    def test_bull_balance_matches_reconciliation(self):
        """bull Σ = −2.57 mbpd (single-baseline reconciliation, ADR-0025)."""
        from nefteboros.forecast.scenarios import (
            FLAG_PRESETS,
            supply_balance_from_flags,
        )

        assert supply_balance_from_flags(FLAG_PRESETS["bull"]) == pytest.approx(-2.57)


# =============================================================================
# (б) Реакция на hormuz — blocked→reopened ⇒ μ падает и 12m прогноз едет вниз
# =============================================================================


class TestHormuzReaction:
    def test_hormuz_reopen_lowers_mu(self):
        from nefteboros.forecast.scenarios import compute_mu_from_flags

        mu_blocked = compute_mu_from_flags("brent", {"hormuz": "blocked"})
        mu_reopen = compute_mu_from_flags("brent", {"hormuz": "full_reopen"})
        assert mu_reopen < mu_blocked

    def test_hormuz_reopen_lowers_12m_forecast(self):
        """Более reopened Hormuz ⇒ ниже μ_0 ⇒ ниже mid 12m OU forecast."""
        from nefteboros.forecast.scenarios import (
            OUParams,
            compute_mu_from_flags,
            compute_ou_forecast,
            get_ou_params,
        )

        spot = 100.0
        base = get_ou_params("brent", "base")

        def mid_12m(flags):
            mu = compute_mu_from_flags("brent", flags)
            p = OUParams(mu_0=mu, theta=base.theta, sigma=base.sigma, inflation=base.inflation)
            return compute_ou_forecast(spot, p, 12).mid

        assert mid_12m({"hormuz": "full_reopen"}) < mid_12m({"hormuz": "blocked"})

    def test_hormuz_ladder_monotone(self):
        """blocked > partial_reopen > full_reopen по μ (больше supply → ниже μ)."""
        from nefteboros.forecast.scenarios import compute_mu_from_flags

        mus = [
            compute_mu_from_flags("brent", {"hormuz": s})
            for s in ("blocked", "partial_reopen", "full_reopen")
        ]
        assert mus[0] > mus[1] > mus[2]

    def test_hormuz_closure_raises_mu(self):
        """Эскалация (full_closure) ⇒ μ выше base (дефицит)."""
        from nefteboros.forecast.scenarios import compute_mu_from_flags

        mu_base = compute_mu_from_flags("brent", {"hormuz": "blocked"})
        mu_closed = compute_mu_from_flags("brent", {"hormuz": "full_closure"})
        assert mu_closed > mu_base


# =============================================================================
# (в) Инвариант bear < base < bull сохраняется на flag-computed μ
# =============================================================================


class TestScenarioInvariant:
    def test_invariant_on_flag_mu(self):
        from nefteboros.forecast.scenarios import (
            FLAG_PRESETS,
            compute_mu_from_flags,
        )

        for asset in OIL:
            bear, base, bull = (
                compute_mu_from_flags(asset, FLAG_PRESETS[s])
                for s in ("bear", "base", "bull")
            )
            assert bear < base < bull, f"{asset}: {bear:.1f}/{base:.1f}/{bull:.1f}"

    def test_invariant_on_12m_forecast(self):
        from nefteboros.forecast.scenarios import (
            OUParams,
            compute_mu_from_flags,
            compute_ou_forecast,
            get_ou_params,
            FLAG_PRESETS,
        )

        spot = 100.0
        for asset in OIL:
            mids = []
            for scen in ("bear", "base", "bull"):
                b = get_ou_params(asset, scen)
                mu = compute_mu_from_flags(asset, FLAG_PRESETS[scen])
                p = OUParams(mu_0=mu, theta=b.theta, sigma=b.sigma, inflation=b.inflation)
                mids.append(compute_ou_forecast(spot, p, 12).mid)
            assert mids[0] < mids[1] < mids[2], f"{asset} 12m mids: {mids}"


# =============================================================================
# Валидация и обратная совместимость
# =============================================================================


class TestFlagValidation:
    def test_unknown_driver_raises(self):
        from nefteboros.forecast.scenarios import compute_mu_from_flags

        with pytest.raises(ValueError, match="Unknown driver"):
            compute_mu_from_flags("brent", {"nonsense": "x"})

    def test_unknown_state_raises(self):
        from nefteboros.forecast.scenarios import compute_mu_from_flags

        with pytest.raises(ValueError, match="Unknown state"):
            compute_mu_from_flags("brent", {"hormuz": "exploded"})

    def test_non_oil_asset_raises(self):
        from nefteboros.forecast.scenarios import compute_mu_from_flags

        for asset in ("henry_hub", "ttf", "moexog", "gazp", "nvtk"):
            with pytest.raises(ValueError, match="только нефть"):
                compute_mu_from_flags(asset, {"hormuz": "full_reopen"})

    def test_partial_flags_default_to_base(self):
        """Неуказанные драйверы = base-состояние (Δ=0)."""
        from nefteboros.forecast.scenarios import (
            compute_mu_from_flags,
            supply_balance_from_flags,
        )

        # только hormuz задан, остальные → base (0)
        assert supply_balance_from_flags({"hormuz": "partial_reopen"}) == pytest.approx(1.5)
        # пустой набор → base equilibrium
        assert compute_mu_from_flags("brent", {}) == pytest.approx(98.0)

    def test_default_ou_params_unchanged(self):
        """get_ou_params (flag_states=None path) идентичен ASSET_PARAMS — backward-compat."""
        from nefteboros.forecast.scenarios import ASSET_PARAMS, get_ou_params

        for asset in OIL:
            for scen in ("bear", "base", "bull"):
                assert get_ou_params(asset, scen) == ASSET_PARAMS[asset][scen]


# =============================================================================
# Network — forecast() с flag_states end-to-end
# =============================================================================


@pytest.mark.network
class TestForecastFlagStatesEndToEnd:
    def test_flag_states_none_identical_to_default(self):
        """flag_states=None ⇒ bit-identical с вызовом без параметра (backward-compat)."""
        from nefteboros.forecast import forecast

        a = forecast("brent", "3m", scenario="base", history_years=2.0)
        b = forecast("brent", "3m", scenario="base", history_years=2.0, flag_states=None)
        assert a.end_point.value == b.end_point.value

    def test_hormuz_reopen_lowers_forecast(self):
        from nefteboros.forecast import forecast

        base = forecast("brent", "12m", history_years=2.0)
        reopened = forecast(
            "brent", "12m", history_years=2.0, flag_states={"hormuz": "full_reopen"}
        )
        assert reopened.end_point.value < base.end_point.value

    def test_flag_states_non_oil_refusal(self):
        from nefteboros.forecast import forecast
        from nefteboros.forecast.schema import ForecastRefusal

        result = forecast("henry_hub", "3m", flag_states={"hormuz": "full_reopen"})
        assert isinstance(result, ForecastRefusal)
