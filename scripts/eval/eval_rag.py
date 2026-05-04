"""Eval RAG retriever: hit@k, MRR, recall@k.

PLACEHOLDER. Реальная реализация — в PR `feature/rag-pipeline`.

Датасет: datasets/rag_qa.jsonl
  Каждая строка: {"question": str, "expected_chunks": [chunk_id], "report": str}

Метрики:
  hit@k       — доля вопросов, у которых правильный чанк попал в top-k
  MRR         — средняя обратная позиция первого правильного чанка
  recall@k    — доля релевантных чанков, попавших в top-k

См. docs/experiments/design.md §1.
"""


def main() -> int:
    raise NotImplementedError("eval_rag — заглушка, см. PR feature/rag-pipeline")


if __name__ == "__main__":
    raise SystemExit(main())
