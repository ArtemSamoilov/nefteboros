"""Eval LLM comparison — все доступные модели на синтезе ответа аналитика.

PLACEHOLDER. Реальная реализация — в PR `feature/eval-llm`.

Сравниваем:
  GigaChat-Max, GigaChat-Ultra,
  kimi-k2p6, kimi-k2p5,
  glm-5p1, glm-5,
  deepseek-v4-pro, deepseek-v3p2, deepseek-v3p1,
  minimax-m2p7, gpt-oss-120b

Датасет: datasets/e2e_dialogues.jsonl

Метрики:
  latency_p50, latency_p95   — мс на запрос
  cost_per_query              — рубли (GigaChat) и USD (Cloud.ru)
  faithfulness                — LLM-as-judge или ручная разметка
  helpfulness                 — субъективная оценка по rubric

Output: metrics/runs/<date>_llm-comparison_<commit>.json + сводная таблица в
docs/experiments/llm-comparison.md.

См. docs/experiments/design.md §5.
"""


def main() -> int:
    raise NotImplementedError("eval_llm — заглушка")


if __name__ == "__main__":
    raise SystemExit(main())
