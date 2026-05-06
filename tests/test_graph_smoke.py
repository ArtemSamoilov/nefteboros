"""Smoke-тесты для analyst LangGraph subgraph.

Каждый тест мокает forecast() и LLM (ouroboros.llm.LLMClient.chat_async),
чтобы не делать сетевых вызовов. Цель — проверить wiring графа:
правильный routing, узлы вызываются в нужном порядке, refusal-paths
не дёргают forecast/LLM, ошибки внутри forecast не валят граф.

См. ADR-0014.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from nefteboros.forecast.schema import (
    ConfidenceInterval,
    ForecastPoint,
    ForecastResult,
    Horizon,
    ModelMethod,
)
from nefteboros.graphs.analyst_graph import build_analyst_graph
from nefteboros.graphs.state import GraphState


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def fake_brent_3m_result() -> ForecastResult:
    return ForecastResult(
        asset="brent",
        horizon=Horizon.M3,
        method=ModelMethod.SARIMAX,
        points=[
            ForecastPoint(
                date=datetime(2026, 8, 6, tzinfo=timezone.utc),
                value=85.50,
                ci_80=ConfidenceInterval(level=0.80, low=70.0, high=101.0),
                ci_95=ConfidenceInterval(level=0.95, low=60.0, high=111.0),
            ),
        ],
        interpretation="Test forecast for smoke.",
        backtest_summary=None,
        metadata={
            "primary_source": "yfinance",
            "data_n_points": 1257,
            "data_first_observation": "2021-05-01",
            "data_last_observation": "2026-05-06",
            "data_last_value": 109.87,
            "log_transform_applied": False,
            "history_years_requested": 5.0,
        },
    )


@pytest.fixture
def fake_llm_text() -> str:
    return (
        "Brent на 3 месяца: $85.50, CI 80% [$70.00, $101.00]. "
        "[forecast_model:brent@3m, sarimax, ADR-0012]\n\n"
        "> Pending: RAG / web overlay в следующих PR'ах."
    )


# =============================================================================
# Tests
# =============================================================================


def test_smoke_forecast_simple_brent(monkeypatch, fake_brent_3m_result, fake_llm_text):
    """Полный путь: 'прогноз нефти на квартал' → classify → forecast → synthesize → validate."""

    def fake_forecast(asset, horizon, **kwargs):
        if asset == "brent" and horizon == "3m":
            return fake_brent_3m_result
        raise ValueError(f"unexpected forecast call: asset={asset!r}, horizon={horizon!r}")

    monkeypatch.setattr("nefteboros.forecast.api.forecast", fake_forecast)

    async def fake_chat_async(self, *args, **kwargs):
        return ({"role": "assistant", "content": fake_llm_text}, {})

    from ouroboros.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat_async", fake_chat_async)

    graph = build_analyst_graph()
    final = asyncio.run(graph.ainvoke(GraphState(query="прогноз нефти на квартал")))

    assert final["intent"] is not None
    assert final["intent"].matched_rule == "rule_1_oil_default"
    assert final["intent"].forecast_assets == ["brent"]
    assert final["intent"].forecast_horizon == Horizon.M3
    assert len(final["forecast_results"]) == 1
    assert final["synthesis"]
    assert "85" in final["synthesis"]
    assert any(c.kind == "forecast_model" for c in final["citations"])
    assert final["validation_warnings"] == [] or all(
        "hallucinated" not in w.lower() for w in final["validation_warnings"]
    )


def test_smoke_russian_gas_refusal_skips_forecast_and_llm(monkeypatch):
    """Russian gas refusal → synthesize не вызывает LLM, forecast пропускается."""

    def boom_forecast(*args, **kwargs):
        raise AssertionError("forecast() должен быть пропущен на refusal-пути")

    monkeypatch.setattr("nefteboros.forecast.api.forecast", boom_forecast)

    async def boom_llm(self, *args, **kwargs):
        raise AssertionError("LLM не должна вызываться на russian_gas_refusal")

    from ouroboros.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat_async", boom_llm)

    graph = build_analyst_graph()
    final = asyncio.run(
        graph.ainvoke(GraphState(query="прогноз цены газа в России в рублях за тыс.м³"))
    )

    assert final["intent"].type.value == "russian_gas_refusal"
    assert final["forecast_results"] == []
    assert "TTF" in final["synthesis"]
    assert final["citations"] == []


def test_smoke_out_of_scope_skips_forecast_and_synthesize_llm(monkeypatch):
    """no_keyword_match идёт в llm_disambiguate; если LLM тоже out_of_scope —
    forecast пропускается, synthesize-LLM не вызывается (refusal path).

    Здесь явно мокаем GigaChat возвращающим out_of_scope, чтобы зафиксировать
    «и rule-based, и LLM говорят: вне области» — ожидаемое поведение для
    запроса вроде «погода в Москве»."""

    from nefteboros.graphs.nodes.llm_disambiguate import _LLMIntent
    from nefteboros.graphs.state import IntentType

    class _FakeStructured:
        async def ainvoke(self, messages):
            return _LLMIntent(
                type=IntentType.OUT_OF_SCOPE,
                refuse_reason="Запрос вне нефтегазовой темы",
            )

    class _FakeChat:
        def with_structured_output(self, schema):
            return _FakeStructured()

    monkeypatch.setattr(
        "nefteboros.llm.gigachat.get_gigachat_chat_model",
        lambda **kwargs: _FakeChat(),
    )

    def boom_forecast(*args, **kwargs):
        raise AssertionError("forecast() не должен вызываться на out_of_scope")

    monkeypatch.setattr("nefteboros.forecast.api.forecast", boom_forecast)

    async def boom_llm(self, *args, **kwargs):
        raise AssertionError("synthesize-LLM не должна вызываться на refusal-пути")

    from ouroboros.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat_async", boom_llm)

    graph = build_analyst_graph()
    final = asyncio.run(graph.ainvoke(GraphState(query="погода в Москве")))

    assert final["intent"].type.value == "out_of_scope"
    assert final["intent"].matched_rule == "llm_out_of_scope"
    assert final["synthesis"]


def test_smoke_horizon_24m_refusal_skips_forecast(monkeypatch):
    """24m horizon → out_of_scope (rule #3), forecast пропускается."""

    def boom_forecast(*args, **kwargs):
        raise AssertionError("forecast() не должен вызываться при >=18m")

    monkeypatch.setattr("nefteboros.forecast.api.forecast", boom_forecast)

    async def noop_llm(self, *args, **kwargs):
        return ({"content": "should not be called"}, {})

    from ouroboros.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat_async", noop_llm)

    graph = build_analyst_graph()
    final = asyncio.run(graph.ainvoke(GraphState(query="прогноз brent на 24 месяца")))

    assert final["intent"].type.value == "out_of_scope"
    assert final["intent"].matched_rule == "rule_3_horizon"
    assert "сценари" in final["synthesis"]


def test_smoke_forecast_runtime_error_is_swallowed(monkeypatch, fake_llm_text):
    """forecast() падает с RuntimeError — узел не должен валить граф;
    synthesize всё равно отработает (с пустым forecast_results)."""

    def angry_forecast(asset, horizon, **kwargs):
        raise RuntimeError(f"данные недоступны для {asset}")

    monkeypatch.setattr("nefteboros.forecast.api.forecast", angry_forecast)

    async def fake_chat_async(self, *args, **kwargs):
        return ({"content": fake_llm_text}, {})

    from ouroboros.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat_async", fake_chat_async)

    graph = build_analyst_graph()
    final = asyncio.run(graph.ainvoke(GraphState(query="прогноз нефти на квартал")))

    assert final["forecast_results"] == []
    assert any("RuntimeError" in e for e in final["forecast_errors"])
    assert final["synthesis"]


def test_smoke_ru_context_calls_forecast_three_times(monkeypatch, fake_brent_3m_result, fake_llm_text):
    """РФ-контекст → 3 актива (brent, urals, urals_minfin_blend) → 3 forecast()."""

    called_assets = []

    def fake_forecast(asset, horizon, **kwargs):
        called_assets.append(asset)
        # Возвращаем тот же fixture с подменённым asset (для smoke достаточно)
        return fake_brent_3m_result.model_copy(update={"asset": asset})

    monkeypatch.setattr("nefteboros.forecast.api.forecast", fake_forecast)

    async def fake_chat_async(self, *args, **kwargs):
        return ({"content": fake_llm_text}, {})

    from ouroboros.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat_async", fake_chat_async)

    graph = build_analyst_graph()
    final = asyncio.run(
        graph.ainvoke(GraphState(query="прогноз нефти для бюджета РФ на год"))
    )

    assert called_assets == ["brent", "urals", "urals_minfin_blend"]
    assert len(final["forecast_results"]) == 3
    assert final["intent"].type.value == "forecast_with_context"
    assert final["intent"].forecast_horizon == Horizon.M12


def test_smoke_llm_disambiguate_routes_to_forecast(monkeypatch, fake_brent_3m_result, fake_llm_text):
    """no_keyword_match → llm_disambiguate (mock GigaChat) → forecast_call → synthesize."""

    from nefteboros.graphs.nodes.llm_disambiguate import _LLMIntent
    from nefteboros.graphs.state import IntentType

    class _FakeStructured:
        async def ainvoke(self, messages):
            return _LLMIntent(
                type=IntentType.FORECAST_SIMPLE,
                assets=["brent"],
                horizon="3m",
            )

    class _FakeChat:
        def with_structured_output(self, schema):
            return _FakeStructured()

    monkeypatch.setattr(
        "nefteboros.llm.gigachat.get_gigachat_chat_model",
        lambda **kwargs: _FakeChat(),
    )

    def fake_forecast(asset, horizon, **kwargs):
        if asset == "brent" and horizon == "3m":
            return fake_brent_3m_result
        raise ValueError(f"unexpected forecast call: asset={asset!r}, horizon={horizon!r}")

    monkeypatch.setattr("nefteboros.forecast.api.forecast", fake_forecast)

    async def fake_chat_async(self, *args, **kwargs):
        return ({"content": fake_llm_text}, {})

    from ouroboros.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat_async", fake_chat_async)

    graph = build_analyst_graph()
    final = asyncio.run(
        graph.ainvoke(GraphState(query="прогноз чёрного золота на квартал"))
    )

    # Rule-based не классифицировал → no_keyword_match → llm_disambiguate →
    # LLM сказал forecast_simple [brent] 3m → forecast_call → synthesize
    assert final["intent"].matched_rule == "llm_forecast_simple"
    assert final["intent"].forecast_assets == ["brent"]
    assert final["intent"].forecast_horizon == Horizon.M3
    assert len(final["forecast_results"]) == 1
    assert final["synthesis"]


def test_smoke_llm_disambiguate_unavailable_falls_back_to_synthesize(monkeypatch):
    """GigaChat creds не заданы → llm_disambiguate fallback → state.intent остаётся
    out_of_scope → synthesize выдаёт refuse_reason без forecast/LLM."""

    def angry(**kwargs):
        raise ValueError("GIGACHAT_CREDENTIALS env not set")

    monkeypatch.setattr(
        "nefteboros.llm.gigachat.get_gigachat_chat_model",
        angry,
    )

    def boom_forecast(*args, **kwargs):
        raise AssertionError("forecast() не должен вызываться при llm fallback")

    monkeypatch.setattr("nefteboros.forecast.api.forecast", boom_forecast)

    async def boom_llm(self, *args, **kwargs):
        raise AssertionError("synthesize-LLM не должна вызываться на out_of_scope")

    from ouroboros.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat_async", boom_llm)

    graph = build_analyst_graph()
    final = asyncio.run(
        graph.ainvoke(GraphState(query="странный запрос вне keyword-набора"))
    )

    assert final["intent"].type.value == "out_of_scope"
    assert final["intent"].matched_rule == "llm_unavailable_creds"
    assert final["synthesis"]
