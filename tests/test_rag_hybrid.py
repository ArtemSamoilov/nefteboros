"""Тесты hybrid sparse+dense retrieval (см. ADR-0027).

Покрывают:
  - RU/EN токенизацию с лемматизацией (text_norm);
  - математику слияния рангов (fusion: RRF + weighted) — детерминированно, без данных;
  - интеграцию fusion в Retriever._hybrid_fuse — на синтетических хитах, без модели/Chroma;
  - BM25 sparse self-retrieval — на реальном корпусе (skip, если data/chunks пуст).
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

from nefteboros.rag.fusion import reciprocal_rank_fusion, weighted_score_fusion
from nefteboros.rag.store import SearchHit
from nefteboros.rag.text_norm import lemmatization_available, tokenize

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = ROOT / "data" / "chunks"


# ---------------------------------------------------------------- text_norm

def test_tokenize_ru_lemmatization():
    """Кириллица лемматизируется: разные формы → один лемма-терм."""
    if not lemmatization_available():
        pytest.skip("pymorphy3 недоступен — лемматизация отключена")
    assert tokenize("ценах")[0] == "цена"
    assert tokenize("санкциями")[0] == "санкция"
    # Разные словоформы 'нефть' схлопываются в один терм.
    assert tokenize("нефти")[0] == tokenize("нефтью")[0] == "нефть"


def test_tokenize_drops_ru_stopwords():
    toks = tokenize("он на я и в во не что")
    assert toks == [], f"стоп-слова должны быть выброшены, получено: {toks}"


def test_tokenize_keeps_digits():
    """Годы/числа критичны для финансовых запросов — не выбрасываем."""
    toks = tokenize("выручка за 2024 год составила 100 млрд")
    assert "2024" in toks
    assert "100" in toks


def test_tokenize_en_lowercase_and_stopwords():
    toks = tokenize("The Oil Sanctions and the Price Cap")
    assert "the" not in toks and "and" not in toks
    assert "oil" in toks and "price" in toks


def test_tokenize_drops_single_chars():
    # 'p' из 'p.71' — одиночная буква, шум; '71' — цифра, сохраняется.
    toks = tokenize("p.71")
    assert "p" not in toks
    assert "71" in toks


# ------------------------------------------------------------------- fusion

def test_rrf_ordering_is_deterministic():
    """RRF: документ в верхах ОБОИХ списков обгоняет тех, кто высоко в одном."""
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60)
    ids = [doc for doc, _ in fused]
    # b: rank2+rank1; a: rank1+rank3; c: rank3+rank2 → b > a > c
    assert ids == ["b", "a", "c"]


def test_rrf_missing_in_one_list():
    """ID только в одном списке получает вклад лишь от него (rank=∞ во втором)."""
    fused = dict(reciprocal_rank_fusion([["x", "y"], ["x"]], k=60))
    # x в обоих (rank1+rank1), y только в первом (rank2)
    assert fused["x"] == pytest.approx(1 / 61 + 1 / 61)
    assert fused["y"] == pytest.approx(1 / 62)
    assert fused["x"] > fused["y"]


def test_weighted_fusion_alpha_balance():
    dense = [("a", 1.0), ("b", 0.0)]   # норм: a=1, b=0
    sparse = [("a", 0.0), ("b", 10.0)]  # норм: a=0, b=1
    # alpha=0.5 → ничья; берём alpha=0.8 → dense перевешивает → a первый
    fused = dict(weighted_score_fusion(dense, sparse, alpha=0.8))
    assert fused["a"] == pytest.approx(0.8)
    assert fused["b"] == pytest.approx(0.2)


def test_weighted_fusion_degenerate_equal_scores():
    """Все скоры равны → нормализация даёт 1.0, не делит на ноль."""
    fused = dict(weighted_score_fusion([("a", 5.0), ("b", 5.0)], [], alpha=0.5))
    assert fused["a"] == pytest.approx(0.5)


# ----------------------------------------------------- Retriever._hybrid_fuse

def test_hybrid_fuse_pulls_in_sparse_only_chunk():
    """Sparse приносит нового кандидата, которого не было в dense (RRF)."""
    from nefteboros.rag.retriever import Retriever
    from nefteboros.rag.sparse_index import SparseHit

    dense = [
        SearchHit("d1", 0.9, "dense one", {"source_id": "S1"}),
        SearchHit("d2", 0.8, "dense two", {"source_id": "S2"}),
    ]
    sparse = [
        SparseHit("sp1", 12.0, "sparse only", {"source_id": "S9"}),
        SearchHit("d1", 5.0, "dense one", {"source_id": "S1"}),  # пересечение
    ]
    fused = Retriever()._hybrid_fuse(
        dense, sparse, fusion="rrf", rrf_k=60, alpha=0.5, top_k=10
    )
    ids = [h.chunk_id for h in fused]
    assert "sp1" in ids, "sparse-only чанк должен попасть в результат"
    assert "d1" in ids and "d2" in ids
    # d1 в обоих списках → должен быть первым
    assert ids[0] == "d1"
    # метаданные sparse-only чанка сохранены
    sp = next(h for h in fused if h.chunk_id == "sp1")
    assert sp.metadata["source_id"] == "S9"


def test_hybrid_fuse_restrict_ids_drops_sparse_only():
    """При активном `where` (restrict_ids) sparse-only чанки отбрасываются."""
    from nefteboros.rag.retriever import Retriever
    from nefteboros.rag.sparse_index import SparseHit

    dense = [SearchHit("d1", 0.9, "x", {"source_id": "S1"})]
    sparse = [SparseHit("sp1", 12.0, "y", {"source_id": "S9"})]
    fused = Retriever()._hybrid_fuse(
        dense, sparse, fusion="rrf", rrf_k=60, alpha=0.5, top_k=10,
        restrict_ids={"d1"},
    )
    ids = [h.chunk_id for h in fused]
    assert ids == ["d1"], "sparse-only вне restrict_ids не должен попасть"


# -------------------------------------------------------- BM25 на реальном корпусе

@pytest.fixture(scope="module")
def sparse_index():
    if not list(CHUNKS_DIR.glob("*.jsonl")):
        pytest.skip("data/chunks пуст — нечего индексировать")
    from nefteboros.rag.sparse_index import SparseIndex

    return SparseIndex.get()


def test_sparse_index_self_retrieval(sparse_index):
    """BM25 должен ранжировать чанк высоко по его же тексту (sanity-инвариант)."""
    files = sorted(glob.glob(str(CHUNKS_DIR / "*.jsonl")))
    first = json.loads(Path(files[0]).read_text(encoding="utf-8").splitlines()[0])
    hits = sparse_index.search(first["text"][:600], k=5)
    assert hits, "BM25 не вернул хитов на собственном тексте чанка"
    top_ids = [h.chunk_id for h in hits[:3]]
    assert first["id"] in top_ids, (
        f"чанк {first['id']} не в top-3 self-retrieval: {top_ids}"
    )


def test_sparse_index_scores_descending(sparse_index):
    hits = sparse_index.search("price cap российская нефть санкции", k=10)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(s > 0 for s in scores), "должны возвращаться только score>0"
