"""Eval anti-hallucination validator цитат.

PLACEHOLDER. Реальная реализация — в PR `feature/citations-validator`.

Датасет: datasets/citations_gold.jsonl
  Каждая строка: {"answer": str, "rag_chunks": [...], "valid_citations": [...]}

Метрики:
  precision               — из заявленных в ответе источников сколько реально подтверждено
  recall                  — из реально подтверждённых сколько отмечено в ответе
  false-attribution rate  — доля цитат с неверным источником

См. docs/experiments/design.md §3.
"""


def main() -> int:
    raise NotImplementedError("eval_citations — заглушка")


if __name__ == "__main__":
    raise SystemExit(main())
