"""Tests для nefteboros.citations.patterns — три regex'а v2.0.0.

Покрытие:
  - RAG: запятые в title, диапазон страниц, кириллические/латинские names
  - Web: markdown-формат с url + hostname + ", web" tail
  - Forecast: одинарный/двойной CI, разные model names
  - Negative cases: false-positive от похожих, но не валидных форматов
  - Integral precision/recall на 20+ размеченных realistic ответах

Требование roadmap-v2.1 D6: precision/recall ≥95% на размеченном корпусе.
"""
from __future__ import annotations

import pytest

from nefteboros.citations.patterns import (
    parse_forecast_citations,
    parse_rag_citations,
    parse_web_citations,
)


# =============================================================================
# RAG pattern
# =============================================================================


class TestRagPattern:
    """RAG: ``[Source title, p.X]`` или ``[Source title, p.X-Y]``."""

    @pytest.mark.parametrize(
        "text, expected_source, expected_start, expected_end",
        [
            # Базовый формат — одностраничная цитата
            (
                "Источник: [OPEC MOMR март 2026, p.14]",
                "OPEC MOMR март 2026", 14, None,
            ),
            # Диапазон страниц
            (
                "Видим в [Новатэк AR-2024, p.5-10] что добыча...",
                "Новатэк AR-2024", 5, 10,
            ),
            # Запятая в title (CRS edge case из manifest.yml)
            (
                "Согласно [CRS — U.S. Conflict with Iran (March 26, 2026), p.5].",
                "CRS — U.S. Conflict with Iran (March 26, 2026)", 5, None,
            ),
            # Несколько запятых в title
            (
                "См. [CRS — Iran Conflict and the Strait of Hormuz: Impacts on Oil, Gas, Other Commodities, p.20].",
                "CRS — Iran Conflict and the Strait of Hormuz: Impacts on Oil, Gas, Other Commodities", 20, None,
            ),
            # Длинное русское название
            (
                "В [Энергетическая стратегия Российской Федерации до 2050 года, p.1-3] указано...",
                "Энергетическая стратегия Российской Федерации до 2050 года", 1, 3,
            ),
            # Скобки в title
            (
                "По [OPEC World Oil Outlook 2025 (full), p.234] спрос растёт.",
                "OPEC World Oil Outlook 2025 (full)", 234, None,
            ),
            # Em-dash и слеш в title
            (
                "[Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap, p.7]",
                "Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap", 7, None,
            ),
            # Кавычки/специальные в РФ-источниках
            (
                "[Газпром — Бухгалтерская (РСБУ) отчётность 2024, p.42]",
                "Газпром — Бухгалтерская (РСБУ) отчётность 2024", 42, None,
            ),
        ],
    )
    def test_positive_extraction(
        self,
        text: str,
        expected_source: str,
        expected_start: int,
        expected_end: int | None,
    ) -> None:
        cites = list(parse_rag_citations(text))
        assert len(cites) == 1, f"expected exactly 1 citation, got {len(cites)}"
        c = cites[0]
        assert c.source == expected_source
        assert c.page_start == expected_start
        assert c.page_end == expected_end

    @pytest.mark.parametrize(
        "text",
        [
            # Плейсхолдер — X не число
            "Шаблон [Source title, p.X]",
            # Без `, p.` — не RAG-формат
            "Просто [скобки в тексте]",
            # `page 5` вместо `p.5` — нестрогий формат, не должен ловиться
            "[Source title, page 5]",
            # `стр.5` (русская локализация) — не v2.0.0 стандарт
            "[Источник, стр.5]",
            # Пустой текст
            "",
            # Просто текст со словом p. внутри
            "См. p.5 в файле",
        ],
    )
    def test_negative_no_extraction(self, text: str) -> None:
        cites = list(parse_rag_citations(text))
        assert len(cites) == 0, f"unexpected citations: {[c.raw for c in cites]}"

    def test_forecast_not_classified_as_rag(self) -> None:
        """Forecast citation начинается с ``[Forecast:`` — RAG-парсер должен
        его пропустить, иначе одна цитата попадёт в два списка."""
        text = "[Forecast: ARIMA, CI 80%]"
        rag_cites = list(parse_rag_citations(text))
        assert len(rag_cites) == 0

    def test_multiple_rag_in_one_answer(self) -> None:
        text = (
            "По [OPEC MOMR март 2026, p.14] добыча выросла, "
            "а [IEA Oil 2025 — Analysis and forecast to 2030, p.33-35] подтверждает."
        )
        cites = list(parse_rag_citations(text))
        assert len(cites) == 2
        assert cites[0].source == "OPEC MOMR март 2026"
        assert cites[1].page_start == 33 and cites[1].page_end == 35


# =============================================================================
# Web pattern
# =============================================================================


class TestWebPattern:
    """Web: ``[title](url) — hostname, web``."""

    @pytest.mark.parametrize(
        "text, expected_title, expected_url, expected_host",
        [
            (
                "См. [OPEC keeps quotas, sources say](https://www.reuters.com/article/opec) — reuters.com, web.",
                "OPEC keeps quotas, sources say",
                "https://www.reuters.com/article/opec",
                "reuters.com",
            ),
            (
                "[ТАСС: бюджет РФ](https://tass.ru/ekonomika/123) — tass.ru, web",
                "ТАСС: бюджет РФ",
                "https://tass.ru/ekonomika/123",
                "tass.ru",
            ),
            # Запятая в title (валидно — markdown допускает)
            (
                "[Title with comma, in middle](https://test.org) — test.org, web",
                "Title with comma, in middle",
                "https://test.org",
                "test.org",
            ),
            # URL с query-параметрами
            (
                "[Bloomberg analytics](https://www.bloomberg.com/news?id=123&lang=en) — bloomberg.com, web",
                "Bloomberg analytics",
                "https://www.bloomberg.com/news?id=123&lang=en",
                "bloomberg.com",
            ),
            # Кириллический title и hostname с RU TLD
            (
                "[Заявление Минфина о бюджете](https://vedomosti.ru/article/2026/05/01) — vedomosti.ru, web",
                "Заявление Минфина о бюджете",
                "https://vedomosti.ru/article/2026/05/01",
                "vedomosti.ru",
            ),
        ],
    )
    def test_positive_extraction(
        self,
        text: str,
        expected_title: str,
        expected_url: str,
        expected_host: str,
    ) -> None:
        cites = list(parse_web_citations(text))
        assert len(cites) == 1, f"expected 1, got {len(cites)}"
        c = cites[0]
        assert c.title == expected_title
        assert c.url == expected_url
        assert c.hostname == expected_host

    @pytest.mark.parametrize(
        "text",
        [
            # Markdown без `, web` хвоста — не v2.0.0 формат
            "[title](https://example.com)",
            # Голый URL без markdown
            "https://reuters.com/article",
            # markdown + hostname но без `web`
            "[title](https://example.com) — example.com",
            # markdown без hostname
            "[title](https://example.com), web",
            # http (HTTP не https) — допустим, но без хвоста
            "[old](http://archaic.example) - oldsite, web",  # тире вместо em-dash
        ],
    )
    def test_negative_no_extraction(self, text: str) -> None:
        cites = list(parse_web_citations(text))
        assert len(cites) == 0, f"unexpected: {[c.raw for c in cites]}"

    def test_multiple_web_citations(self) -> None:
        text = (
            "Согласно [Reuters](https://reuters.com/a) — reuters.com, web "
            "и [Bloomberg analytics](https://bloomberg.com/b) — bloomberg.com, web."
        )
        cites = list(parse_web_citations(text))
        assert len(cites) == 2
        assert cites[0].hostname == "reuters.com"
        assert cites[1].hostname == "bloomberg.com"


# =============================================================================
# Forecast pattern
# =============================================================================


class TestForecastPattern:
    """Forecast: ``[Forecast: model, CI X%]``."""

    @pytest.mark.parametrize(
        "text, expected_model, expected_ci",
        [
            # ADR-пример (sarimax под label ARIMA — текущий v2.0.0 рассинхрон)
            ("[Forecast: ARIMA, CI 80%]", "ARIMA", "80%"),
            ("Прогноз [Forecast: SARIMAX, CI 95%] показывает...", "SARIMAX", "95%"),
            # Двойной CI — формат из roadmap D5
            ("[Forecast: ensemble, CI 80/95%]", "ensemble", "80/95%"),
            # Lowercase model
            ("[Forecast: random_walk, CI 80%]", "random_walk", "80%"),
            ("[Forecast: xgboost, CI 95%]", "xgboost", "95%"),
            ("[Forecast: GBR, CI 80%]", "GBR", "80%"),
        ],
    )
    def test_positive_extraction(
        self, text: str, expected_model: str, expected_ci: str
    ) -> None:
        cites = list(parse_forecast_citations(text))
        assert len(cites) == 1
        c = cites[0]
        assert c.model == expected_model
        assert c.ci == expected_ci

    @pytest.mark.parametrize(
        "text",
        [
            # Без `Forecast:` или `forecast_model:` префикса
            "[ARIMA, CI 80%]",
            # Spec-формат без CI
            "[Forecast: model]",
            # CI без процента
            "[Forecast: model, CI 80]",
            # CI=80% (с равно вместо пробела) — нестрогий формат
            "[Forecast: model, CI=80%]",
            # Пустой текст
            "",
        ],
    )
    def test_negative_no_extraction(self, text: str) -> None:
        cites = list(parse_forecast_citations(text))
        assert len(cites) == 0, f"unexpected: {[c.raw for c in cites]}"

    @pytest.mark.parametrize(
        "text, expected_model",
        [
            # Реальный формат `synthesize` ноды (расхождение со spec)
            ("[forecast_model:brent@3m, ensemble, ADR-0012]", "ensemble"),
            ("[forecast_model:urals@6m, sarimax, ADR-0012]", "sarimax"),
            ("[forecast_model:brent@3m]", "brent@3m"),  # без method и ADR
            ("[forecast_model:urals_minfin_blend@12m]", "urals_minfin_blend@12m"),
        ],
    )
    def test_real_synthesize_format(self, text: str, expected_model: str) -> None:
        """В v2.0.0 synthesize.py пишет `[forecast_model:asset@horizon, ...]`,
        не `[Forecast: model, CI X%]` как в SYSTEM.md. Regex покрывает оба."""
        cites = list(parse_forecast_citations(text))
        assert len(cites) == 1
        assert cites[0].model == expected_model

    @pytest.mark.parametrize(
        "text, expected_model, expected_scenario, expected_ci",
        [
            # Новый production формат после Track A / ADR-0024
            (
                "[Forecast: ou_regime, scenario=base, CI 80%]",
                "ou_regime", "base", "80%",
            ),
            (
                "[Forecast: ou_regime, scenario=bear, CI 95%]",
                "ou_regime", "bear", "95%",
            ),
            (
                "[Forecast: ou_regime, scenario=bull, CI 80/95%]",
                "ou_regime", "bull", "80/95%",
            ),
            # В тексте с контекстом
            (
                "Прогноз $80-$120 [Forecast: ou_regime, scenario=base, CI 80%].",
                "ou_regime", "base", "80%",
            ),
            # Spread-вариант (ADR-0024 §Implementation)
            (
                "[Forecast: ou_regime_spread, scenario=bear, CI 80%]",
                "ou_regime_spread", "bear", "80%",
            ),
        ],
    )
    def test_track_a_scenario_format(
        self,
        text: str,
        expected_model: str,
        expected_scenario: str,
        expected_ci: str,
    ) -> None:
        """После Track A (ADR-0024) обязательное поле scenario=name между
        model и CI. D6 regex должен извлекать его в ParsedForecastCitation.scenario.
        """
        cites = list(parse_forecast_citations(text))
        assert len(cites) == 1, f"expected 1 match, got {len(cites)}"
        c = cites[0]
        assert c.model == expected_model
        assert c.scenario == expected_scenario
        assert c.ci == expected_ci

    def test_legacy_format_scenario_is_none(self) -> None:
        """Legacy формат без scenario: ParsedForecastCitation.scenario = None."""
        text = "[Forecast: ensemble, CI 80%]"
        cites = list(parse_forecast_citations(text))
        assert len(cites) == 1
        assert cites[0].scenario is None
        assert cites[0].model == "ensemble"

    def test_synthesize_format_scenario_is_none(self) -> None:
        """Format 2 (synthesize-output) не несёт scenario tag → None."""
        text = "[forecast_model:brent@3m, ou_regime, ADR-0024]"
        cites = list(parse_forecast_citations(text))
        assert len(cites) == 1
        assert cites[0].scenario is None


# =============================================================================
# Integral precision/recall на realistic корпусе
# =============================================================================


# 20 размеченных ответов агента. Каждый: (text, expected_rag_count,
# expected_web_count, expected_forecast_count). Эталон проставлен вручную
# чтением SYSTEM.md и реальных source_titles из manifest.yml.
LABELED_CORPUS: list[tuple[str, int, int, int]] = [
    # 1. Чистый RAG (1 цитата)
    (
        "Согласно [OPEC MOMR март 2026, p.14], добыча ОПЕК+ снизилась.",
        1, 0, 0,
    ),
    # 2. RAG с диапазоном страниц
    (
        "В [Новатэк AR-2024, p.5-10] описана стратегия диверсификации.",
        1, 0, 0,
    ),
    # 3. RAG с запятой в title (CRS-кейс)
    (
        "См. [CRS — U.S. Conflict with Iran (March 26, 2026), p.7].",
        1, 0, 0,
    ),
    # 4. Чистый Web
    (
        "По данным [Reuters: ОПЕК+ продлил квоты](https://reuters.com/article) — reuters.com, web.",
        0, 1, 0,
    ),
    # 5. Чистый Forecast
    (
        "Прогноз: $82-$87/bbl [Forecast: ensemble, CI 80%].",
        0, 0, 1,
    ),
    # 6. Mixed RAG + Forecast
    (
        "По [IEA Oil 2025 — Analysis and forecast to 2030, p.45] спрос вырастет до 105 mbpd. "
        "Точечный прогноз Brent на 6m: $80 [Forecast: ensemble, CI 80/95%].",
        1, 0, 1,
    ),
    # 7. Mixed RAG + Web
    (
        "Отчёт [OPEC Annual Report 2024, p.12] фиксирует, "
        "однако [свежее заявление](https://tass.ru/news/123) — tass.ru, web уточняет.",
        1, 1, 0,
    ),
    # 8. Mixed Web + Forecast
    (
        "[OPEC update](https://reuters.com/a) — reuters.com, web указывает на дефицит. "
        "Brent на 3m: $85 [Forecast: SARIMAX, CI 80%].",
        0, 1, 1,
    ),
    # 9. Тройная цитата (RAG + Web + Forecast в одном ответе)
    (
        "По [Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap, p.18] "
        "и [Bloomberg piece](https://bloomberg.com/x) — bloomberg.com, web, "
        "Urals дисконт стабилизировался. Прогноз на 6m: $63 [Forecast: ensemble, CI 80%].",
        1, 1, 1,
    ),
    # 10. Множественные RAG (2 chunks)
    (
        "Согласно [OPEC MOMR март 2026, p.14] и [IEA Gas 2025 — Analysis and forecasts to 2030, p.22-25].",
        2, 0, 0,
    ),
    # 11. Без цитат — refusal на off-topic
    (
        "Этот вопрос вне моей компетенции. Рекомендую обратиться к метеорологу.",
        0, 0, 0,
    ),
    # 12. Множественные web (свежие новости из двух источников)
    (
        "Текущая ситуация: [Reuters](https://reuters.com/a) — reuters.com, web "
        "и [Vedomosti](https://vedomosti.ru/b) — vedomosti.ru, web.",
        0, 2, 0,
    ),
    # 13. RAG с большим page number
    (
        "В [OPEC World Oil Outlook 2025 (full), p.234] прогноз 2050.",
        1, 0, 0,
    ),
    # 14. Множественные RAG разных источников
    (
        "[Газпром — Годовой отчёт 2024, p.10] и [Лукойл — Годовой отчёт 2024, p.20] "
        "показывают расхождение по экспорту.",
        2, 0, 0,
    ),
    # 15. Forecast с двойным CI (формат D5)
    (
        "Brent на 12m: $76-$92 [Forecast: ensemble, CI 80/95%].",
        0, 0, 1,
    ),
    # 16. Web с длинным URL и query-string
    (
        "[Bloomberg analytics](https://www.bloomberg.com/news/articles/2026-05-01?source=energy) "
        "— bloomberg.com, web указывает...",
        0, 1, 0,
    ),
    # 17. Текст с упоминанием цифр/дат, но без цитат
    (
        "Brent в апреле 2026 торговался около $82. Динамика умеренная.",
        0, 0, 0,
    ),
    # 18. RAG с длинным title и em-dash
    (
        "Согласно [Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap, p.7], "
        "автокоррекция работает.",
        1, 0, 0,
    ),
    # 19. Псевдо-цитата ([Source title, p.X] плейсхолдер) — должен 0
    (
        "Шаблон ответа: [Source title, p.X] заполняется автоматически.",
        0, 0, 0,
    ),
    # 20. Combined: RAG, RAG, Forecast (для multi-tool ТЗ-кейса)
    (
        "По [OPEC MOMR март 2026, p.14] и [IEF Comparative Analysis of Monthly Oil Reports — April 2026, p.3] "
        "балансы сужаются. Прогноз: $84 [Forecast: ARIMA, CI 80%].",
        2, 0, 1,
    ),
    # 21. Edge: цитата на одной из множественных страниц
    (
        "[Энергетическая стратегия Российской Федерации до 2050 года, p.45-50] фиксирует целевые показатели.",
        1, 0, 0,
    ),
    # 22. CRS с двойными запятыми в title
    (
        "См. [CRS — Iran Conflict and the Strait of Hormuz: Impacts on Oil, Gas, Other Commodities, p.15].",
        1, 0, 0,
    ),
]


def _count_extracted(text: str) -> tuple[int, int, int]:
    return (
        len(list(parse_rag_citations(text))),
        len(list(parse_web_citations(text))),
        len(list(parse_forecast_citations(text))),
    )


class TestPrecisionRecall:
    """Integral precision/recall на размеченном корпусе.

    TP = совпадения в каждом из 3 типов цитат на каждом ответе.
    FP = извлечено больше, чем ожидалось (ложное срабатывание).
    FN = извлечено меньше, чем ожидалось (пропуск).
    """

    def test_corpus_precision_recall(self) -> None:
        tp = fp = fn = 0
        per_case_diffs = []
        for idx, (text, exp_rag, exp_web, exp_forecast) in enumerate(LABELED_CORPUS):
            got_rag, got_web, got_forecast = _count_extracted(text)
            for label, expected, got in [
                ("RAG", exp_rag, got_rag),
                ("Web", exp_web, got_web),
                ("Forecast", exp_forecast, got_forecast),
            ]:
                # Match: min(expected, got) — true positives
                matched = min(expected, got)
                tp += matched
                fp += max(got - expected, 0)
                fn += max(expected - got, 0)
                if got != expected:
                    per_case_diffs.append(
                        f"case#{idx} {label}: expected={expected} got={got}"
                    )

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0

        # Печатаем для отладки
        if per_case_diffs:
            print("\nMismatches:")
            for d in per_case_diffs:
                print(f"  {d}")
        print(f"\nprecision={precision:.3f} recall={recall:.3f} tp={tp} fp={fp} fn={fn}")

        assert precision >= 0.95, f"precision {precision:.3f} < 0.95"
        assert recall >= 0.95, f"recall {recall:.3f} < 0.95"

    def test_corpus_size(self) -> None:
        """Sanity check: размеченный корпус ≥20 ответов (D6 acceptance)."""
        assert len(LABELED_CORPUS) >= 20, (
            f"corpus has {len(LABELED_CORPUS)}, expected >= 20"
        )
