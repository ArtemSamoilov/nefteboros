"""Слияние рангов для hybrid-retrieval (см. ADR-0027).

Две стратегии объединения dense- и sparse-списков:

``reciprocal_rank_fusion`` (default) — RRF: score(d) = Σ_r 1/(k + rank_r(d)).
Работает по РАНГАМ, а не по сырым скорам, поэтому устойчив к несравнимым
шкалам cosine-similarity (~[0.3, 0.9]) и BM25 (unbounded, зависит от корпуса).
Один гиперпараметр k (стандарт 60); чем больше k — тем слабее вклад верхних
позиций относительно хвоста.

``weighted_score_fusion`` — min-max нормализация скоров каждого ретривера в
[0, 1] и линейная комбинация alpha·dense + (1-alpha)·sparse. Тонко настраивается,
но требует нормализации и переобучается под маленький synthetic eval-сет —
держим для аблации, не как default.
"""
from __future__ import annotations


def reciprocal_rank_fusion(
    rankings: list[list[str]], *, k: int = 60
) -> list[tuple[str, float]]:
    """RRF по нескольким ранжированным спискам ID.

    Args:
        rankings: список ранжирований; каждое — список chunk_id в порядке
            убывания релевантности.
        k: сглаживающая константа RRF (стандарт 60).

    Returns:
        ``[(chunk_id, rrf_score), ...]`` по убыванию score. ID, отсутствующий в
        каком-то списке, просто не получает вклад от него (эквивалент rank=∞).
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: -item[1])


def _min_max_norm(items: list[tuple[str, float]]) -> dict[str, float]:
    """Нормализует скоры в [0, 1]. Вырожденный случай (все равны) → 1.0."""
    if not items:
        return {}
    values = [score for _, score in items]
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 0:
        return {doc_id: 1.0 for doc_id, _ in items}
    return {doc_id: (score - lo) / span for doc_id, score in items}


def weighted_score_fusion(
    dense: list[tuple[str, float]],
    sparse: list[tuple[str, float]],
    *,
    alpha: float = 0.5,
) -> list[tuple[str, float]]:
    """Взвешенное слияние нормализованных скоров.

    Args:
        dense: ``[(chunk_id, cosine_score), ...]``.
        sparse: ``[(chunk_id, bm25_score), ...]``.
        alpha: вес dense; sparse получает ``1 - alpha``.

    Returns:
        ``[(chunk_id, fused_score), ...]`` по убыванию score.
    """
    norm_dense = _min_max_norm(dense)
    norm_sparse = _min_max_norm(sparse)
    fused: dict[str, float] = {}
    for doc_id in set(norm_dense) | set(norm_sparse):
        fused[doc_id] = (
            alpha * norm_dense.get(doc_id, 0.0)
            + (1.0 - alpha) * norm_sparse.get(doc_id, 0.0)
        )
    return sorted(fused.items(), key=lambda item: -item[1])
