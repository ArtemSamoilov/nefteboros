"""Unit-тесты llm_disambiguate узла.

Реальный GigaChat не вызывается — monkeypatch на
`nefteboros.llm.router.get_chat_model`. Семантика:

- happy-path через `with_structured_output` (langchain helper);
- happy-path через raw chat + JSON parse (когда structured_output не реализован);
- разные виды ошибок: invalid JSON / no creds / API exception / invalid horizon.
"""

from __future__ import annotations

import asyncio
import json

from nefteboros.forecast.schema import Horizon
from nefteboros.graphs.nodes.llm_disambiguate import _LLMIntent, llm_disambiguate
from nefteboros.graphs.state import GraphState, Intent, IntentType


# =============================================================================
# Fixtures (без pytest fixture, чтобы не тащить pytest-asyncio mode)
# =============================================================================


def _state(query: str = "странный запрос вне keyword-набора") -> GraphState:
    return GraphState(
        query=query,
        intent=Intent(
            type=IntentType.OUT_OF_SCOPE,
            refuse_reason="rule-based не классифицировал",
            matched_rule="no_keyword_match",
        ),
    )


class _FakeStructured:
    """Эмулирует object возвращаемый chat.with_structured_output(...)."""

    def __init__(self, response):
        self._response = response

    async def ainvoke(self, messages):
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response


class _FakeChat:
    """Эмулирует langchain BaseChatModel.

    structured_response = _LLMIntent | None — если None, with_structured_output
    бросает NotImplementedError (заставляя узел перейти на raw fallback).
    raw_response = str | Exception — для пути raw fallback.
    """

    def __init__(self, *, structured_response=None, raw_response=None):
        self._structured_response = structured_response
        self._raw_response = raw_response

    def with_structured_output(self, schema):
        if self._structured_response is None:
            raise NotImplementedError("structured output not supported in fake")
        return _FakeStructured(self._structured_response)

    async def ainvoke(self, messages):
        if self._raw_response is None:
            raise RuntimeError("raw response not configured")
        if isinstance(self._raw_response, BaseException):
            raise self._raw_response

        class _Resp:
            content = self._raw_response

        return _Resp()


def _patch_chat(monkeypatch, fake_chat):
    monkeypatch.setattr(
        "nefteboros.llm.router.get_chat_model",
        lambda **kwargs: fake_chat,
    )


# =============================================================================
# Tests
# =============================================================================


def test_structured_output_returns_forecast_simple(monkeypatch):
    """Happy path: with_structured_output работает, LLM → forecast_simple."""
    fake = _FakeChat(
        structured_response=_LLMIntent(
            type=IntentType.FORECAST_SIMPLE,
            assets=["brent"],
            horizon="3m",
            refuse_reason=None,
        ),
    )
    _patch_chat(monkeypatch, fake)

    result = asyncio.run(llm_disambiguate(_state("прогноз чёрного золота на квартал")))

    assert "intent" in result
    assert result["intent"].type == IntentType.FORECAST_SIMPLE
    assert result["intent"].forecast_assets == ["brent"]
    assert result["intent"].forecast_horizon == Horizon.M3
    assert result["intent"].matched_rule == "llm_forecast_simple"


def test_structured_output_returns_with_context(monkeypatch):
    """РФ-контекст через LLM → 3 актива (brent, urals, urals_minfin_blend)."""
    fake = _FakeChat(
        structured_response=_LLMIntent(
            type=IntentType.FORECAST_WITH_CONTEXT,
            assets=["brent", "urals", "urals_minfin_blend"],
            horizon="12m",
        ),
    )
    _patch_chat(monkeypatch, fake)

    result = asyncio.run(
        llm_disambiguate(_state("сколько энергоносители принесут в казну в 2026"))
    )

    assert result["intent"].type == IntentType.FORECAST_WITH_CONTEXT
    assert result["intent"].forecast_assets == ["brent", "urals", "urals_minfin_blend"]
    assert result["intent"].forecast_horizon == Horizon.M12


def test_raw_fallback_when_no_structured_output(monkeypatch):
    """with_structured_output → NotImplementedError → fallback на raw chat + JSON parse."""
    raw = json.dumps(
        {
            "type": "forecast_simple",
            "assets": ["brent"],
            "horizon": "6m",
            "refuse_reason": "brent как proxy для Bonny Light (light sweet)",
        }
    )
    fake = _FakeChat(raw_response=raw)
    _patch_chat(monkeypatch, fake)

    result = asyncio.run(llm_disambiguate(_state("Bonny Light на полгода")))

    assert result["intent"].type == IntentType.FORECAST_SIMPLE
    assert result["intent"].forecast_horizon == Horizon.M6
    assert "Bonny Light" in (result["intent"].refuse_reason or "")


def test_invalid_json_falls_back_to_parse_failed(monkeypatch):
    """LLM вернул не-JSON → matched_rule = llm_parse_failed, intent остаётся."""
    fake = _FakeChat(raw_response="Не могу выполнить просьбу, я модель.")
    _patch_chat(monkeypatch, fake)

    result = asyncio.run(llm_disambiguate(_state()))

    assert result["intent"].type == IntentType.OUT_OF_SCOPE
    assert result["intent"].matched_rule == "llm_parse_failed"


def test_no_creds_falls_back_to_unavailable(monkeypatch):
    """get_gigachat_chat_model падает с ValueError (нет env) → fallback."""

    def angry(**kwargs):
        raise ValueError("GIGACHAT_CREDENTIALS env not set")

    monkeypatch.setattr(
        "nefteboros.llm.router.get_chat_model",
        angry,
    )

    result = asyncio.run(llm_disambiguate(_state()))

    assert result["intent"].matched_rule == "llm_unavailable_creds"


def test_api_error_falls_back_to_llm_error(monkeypatch):
    """API error (network) → fallback c пометкой llm_error_<ExceptionType>."""
    fake = _FakeChat(structured_response=ConnectionError("GigaChat API unreachable"))
    _patch_chat(monkeypatch, fake)

    result = asyncio.run(llm_disambiguate(_state()))

    assert result["intent"].matched_rule == "llm_error_ConnectionError"


def test_invalid_horizon_is_treated_as_null(monkeypatch):
    """LLM вернул horizon='5m' (вне supported set) → forecast_horizon=None,
    остальные поля корректные."""
    raw = json.dumps(
        {
            "type": "forecast_simple",
            "assets": ["brent"],
            "horizon": "5m",
            "refuse_reason": None,
        }
    )
    fake = _FakeChat(raw_response=raw)
    _patch_chat(monkeypatch, fake)

    result = asyncio.run(llm_disambiguate(_state()))

    assert result["intent"].type == IntentType.FORECAST_SIMPLE
    assert result["intent"].forecast_horizon is None
    assert result["intent"].forecast_assets == ["brent"]
