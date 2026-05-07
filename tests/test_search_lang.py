"""Tests для nefteboros.search.lang — детектор языка по кириллице."""
from __future__ import annotations

import pytest

from nefteboros.search.lang import brave_params_for_lang, detect_lang


class TestDetectLang:
    @pytest.mark.parametrize(
        "query, expected",
        [
            ("Что говорит OPEC про квоты", "ru"),
            ("прогноз Brent на 3 месяца", "ru"),
            ("Новак заявил о сокращении добычи", "ru"),
            ("What does OPEC say about quotas", "en"),
            ("Brent crude oil price forecast 3 months", "en"),
            ("US sanctions Gazprom", "en"),
            # Смешанные — побеждает преобладающий
            ("Новатэк LNG strategy", "ru"),
            ("OPEC Россия", "ru"),
            ("OPEC Russia output", "en"),
        ],
    )
    def test_classifies(self, query: str, expected: str) -> None:
        assert detect_lang(query) == expected

    @pytest.mark.parametrize("query", ["", "   ", "123 456", "$$$ %%%", None])
    def test_empty_or_non_alpha_defaults_en(self, query) -> None:
        assert detect_lang(query or "") == "en"


class TestBraveParamsForLang:
    def test_ru_lang_returns_ru_params(self) -> None:
        params = brave_params_for_lang("ru")
        assert params["search_lang"] == "ru"
        assert params["country"] == "RU"
        assert params["ui_lang"] == "ru-RU"

    def test_en_lang_returns_en_params(self) -> None:
        params = brave_params_for_lang("en")
        assert params["search_lang"] == "en"
        assert params["country"] == "US"
        assert params["ui_lang"] == "en-US"

    def test_unknown_lang_falls_back_to_en(self) -> None:
        params = brave_params_for_lang("zh")
        assert params["search_lang"] == "en"
