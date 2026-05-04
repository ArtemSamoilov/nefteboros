"""Сводный дашборд по всем eval-runs.

PLACEHOLDER. Реальная реализация — в PR `feature/eval-dashboard`.

Читает metrics/runs/*.json, формирует:
  docs/experiments/results.md   — markdown-таблица
  docs/experiments/results.png  — графики (если matplotlib доступен)

См. docs/experiments/design.md.
"""


def main() -> int:
    raise NotImplementedError("make_dashboard — заглушка")


if __name__ == "__main__":
    raise SystemExit(main())
