"""Eval end-to-end на golden dialogues.

PLACEHOLDER. Реальная реализация — в PR `feature/eval-e2e`.

Прогоняет полный пайплайн (от запроса пользователя до финального ответа)
на наборе эталонных сценариев, включая 5+ демо из ТЗ §4.6:
  1. Ответ на основе отчёта (RAG)
  2. Ответ на основе веб-поиска (current data)
  3. Комбинированный ответ (RAG + web)
  4. Вызов forecast tool с прогнозом цены
  5. Корректная обработка запроса вне компетенции

Датасет: datasets/e2e_dialogues.jsonl

Метрики:
  success rate          — доля сценариев, где ответ соответствует rubric
  citation correctness  — все ли цитаты валидны (через citations validator)
  latency_full          — время полного ответа (сек)
  fallback rate         — частота скатывания в "не знаю" / web для RAG-вопросов

См. docs/experiments/design.md §6.
"""


def main() -> int:
    raise NotImplementedError("eval_e2e — заглушка")


if __name__ == "__main__":
    raise SystemExit(main())
