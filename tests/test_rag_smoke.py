"""Smoke-тесты RAG-pipeline (этап 3).

Проверяют что 5 запросов (по одному на демо-сценарий ТЗ) находят релевантные
источники в собранной Chroma-коллекции.

Запуск требует собранного индекса:
    python scripts/build_index.py
    pytest tests/test_rag_smoke.py -v -s

Если индекс пуст — тесты пропускаются (skip), не падают.
"""
from __future__ import annotations

import pytest


SMOKE_QUERIES = [
    {
        "query": "Каков прогноз спроса на нефть к 2030 году по версии OPEC?",
        "expected_sources": {"opec_woo_2025", "opec_annual_report_2024", "iea_oil_2025"},
        "demo_scenario": "1. Ответ из отчёта",
    },
    {
        "query": "Что говорит ОПЕК+ про квоты в апреле 2026?",
        "expected_sources": {"opec_annual_report_2024", "ief_momr_comparative_2026-04", "iea_omr_2026-04_free"},
        "demo_scenario": "3. Комбинированный (свежий operational срез)",
    },
    {
        "query": "Как работает price cap на российскую нефть и насколько эффективен?",
        "expected_sources": {"bruegel_wp_2025-32_oil_sanctions", "eu_repower_2025-10_com637"},
        "demo_scenario": "3. Комбинированный (санкции)",
    },
    {
        "query": "Какова стратегия Новатэка по СПГ-проектам — Арктик СПГ-2, Мурманский СПГ?",
        "expected_sources": {"novatek_ar_2024", "giignl_lng_2025"},
        "demo_scenario": "1. Ответ из отчёта (корпоративный)",
    },
    {
        "query": "Что произошло с Ираном в феврале 2026 — атаки на инфраструктуру и влияние на нефть?",
        "expected_sources": {"crs_us_iran_conflict_2026-03", "crs_iran_hormuz_2026"},
        "demo_scenario": "3. Комбинированный (геополитика)",
    },
]


@pytest.fixture(scope="module")
def retriever():
    from nefteboros.rag.retriever import Retriever
    from nefteboros.rag.store import VectorStore

    store = VectorStore.open()
    if store.count() == 0:
        pytest.skip(
            "Vector store пуст — запусти `python scripts/build_index.py` перед тестами"
        )
    return Retriever()


@pytest.mark.parametrize("scenario", SMOKE_QUERIES, ids=lambda s: s["demo_scenario"])
def test_retrieval_finds_relevant_source(retriever, scenario):
    hits = retriever.retrieve(scenario["query"], k_dense=30, k_final=5)
    assert hits, f"Нет hits для query: {scenario['query']!r}"

    found_sources = {h.metadata["source_id"] for h in hits}
    overlap = found_sources & scenario["expected_sources"]

    assert overlap, (
        f"Демо-сценарий {scenario['demo_scenario']!r}:\n"
        f"  query={scenario['query']!r}\n"
        f"  expected ⊂ {scenario['expected_sources']}\n"
        f"  got     = {found_sources}\n"
        f"  top-5 hits:\n"
        + "\n".join(
            f"    {h.rerank_score:+.3f} | {h.metadata['source_id']} | "
            f"{h.metadata.get('section_path', '')[:60]} | {h.text[:80]!r}"
            for h in hits
        )
    )


def test_metadata_filter_region_russia(retriever):
    """Фильтр по region=russia должен возвращать только RU-документы."""
    hits = retriever.retrieve(
        "стратегия развития энергетики",
        k_dense=10,
        k_final=5,
        where={"language": "ru"},
        rerank=False,  # без reranker — быстрее, проверяем чисто фильтр
    )
    assert hits, "Нет hits с фильтром language=ru"
    for h in hits:
        assert h.metadata["language"] == "ru", (
            f"Фильтр language=ru пропустил {h.metadata['language']!r}: "
            f"{h.metadata['source_id']}"
        )
