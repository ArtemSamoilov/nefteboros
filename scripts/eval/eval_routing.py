"""Eval intent classifier (узел classify_intent в analyst_graph).

PLACEHOLDER. Реальная реализация — в PR `feature/langgraph-subgraph`.

Датасет: datasets/routing.jsonl
  Каждая строка: {"query": str, "expected_route": "rag|web|forecast|oos"}

Метрики:
  accuracy            — доля верно классифицированных запросов
  F1 per class
  confusion matrix    — печатается как таблица в metrics/runs/<...>.json

См. docs/experiments/design.md §2.
"""


def main() -> int:
    raise NotImplementedError("eval_routing — заглушка")


if __name__ == "__main__":
    raise SystemExit(main())
