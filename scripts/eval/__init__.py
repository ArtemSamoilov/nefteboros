"""Eval-скрипты для оценки качества подграфов системы.

Каждый скрипт:
  - читает свой датасет из datasets/
  - прогоняет соответствующий компонент
  - считает метрики (см. docs/experiments/design.md)
  - сохраняет результат в metrics/runs/<date>_<component>_<commit>.json
  - печатает summary

Запуск (из корня репо):
  python -m scripts.eval.eval_rag       # RAG retriever (hit@k, MRR)
  python -m scripts.eval.eval_routing   # Intent classifier (accuracy, F1)
  python -m scripts.eval.eval_citations # Citations validator (precision, recall)
  python -m scripts.eval.eval_forecast  # Forecast (MAPE, RMSE, coverage)
  python -m scripts.eval.eval_llm       # LLM comparison (latency, cost, faithfulness)
  python -m scripts.eval.eval_e2e       # End-to-end на golden dialogues
  python -m scripts.eval.make_dashboard # Сборка docs/experiments/results.md
"""
