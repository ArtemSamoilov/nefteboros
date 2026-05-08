#!/usr/bin/env python3
"""End-to-end eval на golden dialogues.

Прогоняет diалоги из ``datasets/e2e_dialogues.jsonl`` через runner и
считает четыре метрики (см. roadmap-v2.1 Track D):

1. **success_rate** — non-refusal-expected диалоги где агент дал
   нон-empty ответ без явного «нет данных» / «не знаю».
2. **citation_correctness** — non-refusal диалоги где все цитаты в
   ответе подтверждены через D6 :func:`nefteboros.citations.validate`.
3. **structure_adherence** — non-refusal диалоги, прошедшие D5
   :func:`scripts.eval.structure.check_structure`.
4. **refusal_rate** — off-topic-expected диалоги где агент корректно
   отказался (ожидаемый 100% по roadmap).

Runner:

- ``--mock`` (default) — :class:`MockRunner` с шаблонными ответами.
  Позволяет валидировать pipeline и схемы без LLM-ключей. Baseline
  в этом режиме измеряет **корректность eval-кода**, а не агента.
- (без флага) — :class:`GraphRunner` через
  :func:`nefteboros.graphs.analyst_graph.build_analyst_graph`. Требует
  ``GIGACHAT_CREDENTIALS`` / ``HYDRA_API_KEY`` / ``BRAVE_API_KEY``.

Use:

    python -m scripts.eval.eval_e2e --mock                 # smoke
    python -m scripts.eval.eval_e2e                        # real (env)
    python -m scripts.eval.eval_e2e --dataset path.jsonl   # custom

Output:

    metrics/runs/<YYYY-MM-DD>_e2e_<commit>.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "datasets" / "e2e_dialogues.jsonl"
METRICS_RUNS_DIR = REPO_ROOT / "metrics" / "runs"


# =============================================================================
# Run result + per-dialogue score
# =============================================================================


@dataclass
class RunResult:
    """Результат одного прогона диалога runner'ом."""

    answer: str
    tools_called: list[str] = field(default_factory=list)
    retrieved_chunks: list[Any] = field(default_factory=list)
    web_results: list[Any] = field(default_factory=list)
    forecast_calls: list[Any] = field(default_factory=list)
    refused: bool = False
    error: Optional[str] = None


@dataclass
class DialogueScore:
    """Per-dialogue метрики."""

    id: str
    scenario_type: str
    held_out: bool
    expected_refusal: bool

    # Применимость per-метрика
    success_applicable: bool
    citation_applicable: bool
    structure_applicable: bool
    refusal_applicable: bool

    # Результаты per-метрика
    success: bool
    citation_correct: bool
    structure_passed: bool
    refusal_correct: bool

    # Детали
    keywords_matched: int = 0
    keywords_total: int = 0
    citation_count: int = 0
    fabricated: list[str] = field(default_factory=list)
    error: Optional[str] = None


# =============================================================================
# Runners
# =============================================================================


class AgentRunner(Protocol):
    """Минимальный контракт runner'а."""

    async def run(self, dialogue: dict) -> RunResult: ...


class MockRunner:
    """Шаблонные ответы для smoke / baseline без LLM-ключей.

    Каждый сценарный тип получает структурно-правдоподобный ответ — он
    не отражает качество настоящего агента, но позволяет проверить что
    eval-код считает метрики корректно (sanity-baseline).

    Шаблоны намеренно следуют SYSTEM.md правилам формата (TL;DR,
    цифры, citations) — иначе structure adherence будет 0% и не
    различишь баг в checker от плохого шаблона.
    """

    _MOCK_BY_SCENARIO: dict[str, dict] = {
        "rag_only": {
            "answer": (
                "OPEC MOMR март 2026 фиксирует снижение квот на 1.4 mbpd.\n\n"
                "Основные тезисы согласно [OPEC MOMR март 2026, p.14]: "
                "сокращение добычи в Q2 2026, продление действия квот."
            ),
            "tools_called": ["rag_search"],
            "retrieved_chunks": [
                {"source_title": "OPEC MOMR март 2026", "page_start": 14, "page_end": 14},
            ],
            "refused": False,
        },
        "web_only": {
            "answer": (
                "Brent торгуется около $82.5/bbl на momentum от заявлений ОПЕК+.\n\n"
                "Свежие данные: [Brent above $82](https://reuters.com/article/x) — reuters.com, web."
            ),
            "tools_called": ["web_search"],
            "web_results": [
                {"url": "https://reuters.com/article/x", "hostname": "reuters.com", "title": "Brent above $82"},
            ],
            "refused": False,
        },
        "rag_plus_web": {
            "answer": (
                "Спрос на нефть в 2026 растёт умеренно: 1.5 mbpd по OPEC, динамика подтверждена СМИ.\n\n"
                "По [OPEC MOMR март 2026, p.10] спрос +1.5 mbpd; "
                "[ОПЕК+ продлил квоты](https://reuters.com/x) — reuters.com, web."
            ),
            "tools_called": ["rag_search", "web_search"],
            "retrieved_chunks": [
                {"source_title": "OPEC MOMR март 2026", "page_start": 10, "page_end": 10},
            ],
            "web_results": [
                {"url": "https://reuters.com/x", "hostname": "reuters.com", "title": "ОПЕК+ продлил квоты"},
            ],
            "refused": False,
        },
        "forecast": {
            "answer": (
                "Brent на 6 месяцев: $80-$88, центр $84.\n\n"
                "Прогноз ensemble [Forecast: ensemble, CI 80%]; основной риск — решение ОПЕК+."
            ),
            "tools_called": ["forecast"],
            "forecast_calls": [{"method": "ensemble", "asset": "brent", "horizon": "6m"}],
            "refused": False,
        },
        "out_of_scope": {
            "answer": (
                "Этот вопрос вне моей компетенции — я отвечаю по нефтегазу. "
                "Рекомендую обратиться к профильному источнику."
            ),
            "tools_called": [],
            "refused": True,
        },
        "multi_tool": {
            "answer": (
                "Санкции 2025 года увеличили Urals discount до $15-$18/bbl.\n\n"
                "Контекст: [Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap, p.7]. "
                "Прогноз Urals на 6m: $63-$68 [Forecast: ensemble, CI 80%]."
            ),
            "tools_called": ["rag_search", "forecast"],
            "retrieved_chunks": [
                {"source_title": "Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap", "page_start": 7, "page_end": 7},
            ],
            "forecast_calls": [{"method": "ensemble", "asset": "urals", "horizon": "6m"}],
            "refused": False,
        },
        "follow_up": {
            "answer": (
                "При снижении квот ОПЕК+ на 1 mbpd Brent сместится к $90-$95.\n\n"
                "Корректировка к baseline'у $80-$88 [Forecast: ensemble, CI 80%]; "
                "источник правил квот [OPEC MOMR март 2026, p.14]."
            ),
            "tools_called": ["forecast", "rag_search"],
            "retrieved_chunks": [
                {"source_title": "OPEC MOMR март 2026", "page_start": 14, "page_end": 14},
            ],
            "forecast_calls": [{"method": "ensemble", "asset": "brent", "horizon": "6m"}],
            "refused": False,
        },
        "unknown_with_hypothesis": {
            "answer": (
                "Снятие санкций с Ирана сократит Urals discount до $5-$10/bbl, но это сценарная гипотеза.\n\n"
                "По [CRS — U.S. Conflict with Iran (March 26, 2026), p.5] возврат 1.5 mbpd иранской "
                "нефти давит на спред Urals/Brent; точечная цифра требует scenario-forecast."
            ),
            "tools_called": ["rag_search"],
            "retrieved_chunks": [
                {"source_title": "CRS — U.S. Conflict with Iran (March 26, 2026)", "page_start": 5, "page_end": 5},
            ],
            "refused": False,
        },
    }

    async def run(self, dialogue: dict) -> RunResult:
        scenario = dialogue.get("scenario_type", "rag_only")
        mock = self._MOCK_BY_SCENARIO.get(scenario, self._MOCK_BY_SCENARIO["rag_only"])
        return RunResult(
            answer=mock["answer"],
            tools_called=list(mock.get("tools_called", [])),
            retrieved_chunks=list(mock.get("retrieved_chunks", [])),
            web_results=list(mock.get("web_results", [])),
            forecast_calls=list(mock.get("forecast_calls", [])),
            refused=mock.get("refused", False),
        )


class GraphRunner:
    """Реальный runner через :func:`build_analyst_graph`. Требует env.

    Не реализован полностью — analyst_graph в v2.0.0 заточен под
    forecast-flow (см. ADR-0014). Полный multi-tool / web /
    follow_up flow требует web_search ноды и multi-turn state, что
    выходит за scope текущего D6/D1 PR.

    На момент Track D-base PR этот runner — заглушка. Полный e2e run
    делается в отдельной сессии после Track B (маршрутизация B1) и
    Track F (Langfuse трейсинг F1).
    """

    async def run(self, dialogue: dict) -> RunResult:
        return RunResult(
            answer="",
            error=(
                "GraphRunner не реализован в Track D-base PR. "
                "Используй --mock для smoke-baseline; полный e2e — "
                "в отдельной сессии после Track B / F."
            ),
        )


# =============================================================================
# Scoring
# =============================================================================


_NEGATIVE_REFUSAL_RE = re.compile(
    r"(нет\s+данн|не\s+знаю|не\s+нашёл|информации\s+нет|данных\s+нет)",
    re.IGNORECASE,
)


def _matches_keyword(answer: str, keyword: str) -> bool:
    """Case-insensitive substring matching по корням слов."""
    return keyword.lower() in answer.lower()


def _is_negative_refusal(answer: str) -> bool:
    """Detects «нет данных» / «не знаю» — запрещённые отказные формы
    (см. roadmap B1: запрет на ответ «нет данных»)."""
    return bool(_NEGATIVE_REFUSAL_RE.search(answer))


def score_dialogue(dialogue: dict, result: RunResult) -> DialogueScore:
    """Считает per-метрики для одного диалога."""
    expected = dialogue["expected_behavior"]
    expected_refusal = bool(expected.get("expected_refusal", False))

    if result.error or not result.answer.strip():
        return DialogueScore(
            id=dialogue["id"],
            scenario_type=dialogue["scenario_type"],
            held_out=bool(dialogue.get("held_out", False)),
            expected_refusal=expected_refusal,
            success_applicable=True,
            citation_applicable=False,
            structure_applicable=False,
            refusal_applicable=expected_refusal,
            success=False,
            citation_correct=False,
            structure_passed=False,
            refusal_correct=False,
            error=result.error or "empty answer",
        )

    # Импорты внутри функции — модуль scripts/eval/structure импортирует
    # nefteboros.citations, и при `pytest` с измёнными PYTHONPATH'ами
    # глобальный импорт мог бы сломать сбор тестов.
    from nefteboros.citations import validate
    from scripts.eval.structure import check_structure

    keywords_total = len(expected.get("expected_keywords", []))
    keywords_matched = sum(
        1 for kw in expected.get("expected_keywords", [])
        if _matches_keyword(result.answer, kw)
    )

    if expected_refusal:
        # off-topic — корректный refusal: либо runner явно пометил, либо
        # эвристика «короткий ответ без tools» (агент не пошёл за данными).
        refusal_correct = result.refused or (
            not result.tools_called and len(result.answer.split()) < 80
        )
        return DialogueScore(
            id=dialogue["id"],
            scenario_type=dialogue["scenario_type"],
            held_out=bool(dialogue.get("held_out", False)),
            expected_refusal=True,
            success_applicable=False,
            citation_applicable=False,
            structure_applicable=False,
            refusal_applicable=True,
            success=False,
            citation_correct=False,
            structure_passed=False,
            refusal_correct=refusal_correct,
            keywords_matched=keywords_matched,
            keywords_total=keywords_total,
        )

    # Non-refusal: оцениваем все три метрики
    success = (
        len(result.answer.strip()) > 0
        and not _is_negative_refusal(result.answer)
        and (keywords_matched >= max(1, keywords_total // 2) if keywords_total else True)
    )

    citation_report = validate(
        result.answer,
        retrieved_chunks=result.retrieved_chunks,
        web_results=result.web_results,
        forecast_calls=result.forecast_calls,
    )
    expected_min = int(expected.get("expected_min_citations", 0))
    citation_correct = (
        citation_report.valid
        and citation_report.total_citations >= expected_min
    )

    structure_report = check_structure(
        result.answer,
        retrieved_chunks=result.retrieved_chunks,
        web_results=result.web_results,
        forecast_calls=result.forecast_calls,
    )

    return DialogueScore(
        id=dialogue["id"],
        scenario_type=dialogue["scenario_type"],
        held_out=bool(dialogue.get("held_out", False)),
        expected_refusal=False,
        success_applicable=True,
        citation_applicable=True,
        structure_applicable=True,
        refusal_applicable=False,
        success=success,
        citation_correct=citation_correct,
        structure_passed=structure_report.passed,
        refusal_correct=False,
        keywords_matched=keywords_matched,
        keywords_total=keywords_total,
        citation_count=citation_report.total_citations,
        fabricated=citation_report.fabricated,
    )


# =============================================================================
# Aggregation
# =============================================================================


def _safe_div(num: int, den: int) -> Optional[float]:
    return num / den if den else None


def aggregate(scores: list[DialogueScore]) -> dict:
    """4 метрики + breakdown по dev / held-out."""
    def metrics_for(subset: list[DialogueScore]) -> dict:
        n = len(subset)
        success_n = sum(1 for s in subset if s.success_applicable)
        citation_n = sum(1 for s in subset if s.citation_applicable)
        structure_n = sum(1 for s in subset if s.structure_applicable)
        refusal_n = sum(1 for s in subset if s.refusal_applicable)
        return {
            "n_dialogues": n,
            "success_rate": _safe_div(
                sum(1 for s in subset if s.success_applicable and s.success),
                success_n,
            ),
            "citation_correctness": _safe_div(
                sum(1 for s in subset if s.citation_applicable and s.citation_correct),
                citation_n,
            ),
            "structure_adherence": _safe_div(
                sum(1 for s in subset if s.structure_applicable and s.structure_passed),
                structure_n,
            ),
            "refusal_rate": _safe_div(
                sum(1 for s in subset if s.refusal_applicable and s.refusal_correct),
                refusal_n,
            ),
            "applicable_counts": {
                "success": success_n,
                "citation": citation_n,
                "structure": structure_n,
                "refusal": refusal_n,
            },
        }

    return {
        "all": metrics_for(scores),
        "dev": metrics_for([s for s in scores if not s.held_out]),
        "held_out": metrics_for([s for s in scores if s.held_out]),
    }


# =============================================================================
# I/O
# =============================================================================


def load_dialogues(path: Path) -> list[dict]:
    dialogues = []
    with path.open(encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                dialogues.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_num} — invalid JSON: {e}")
    return dialogues


def _git_short_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def save_run(
    metrics: dict,
    scores: list[DialogueScore],
    *,
    runner_name: str,
    dataset_path: Path,
) -> Path:
    METRICS_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    commit = _git_short_commit()
    out_path = METRICS_RUNS_DIR / f"{timestamp}_e2e_{runner_name}_{commit}.json"
    payload = {
        "timestamp_utc": timestamp,
        "git_commit": commit,
        "runner": runner_name,
        "dataset": str(dataset_path.relative_to(REPO_ROOT)),
        "metrics": metrics,
        "per_dialogue": [asdict(s) for s in scores],
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return out_path


# =============================================================================
# CLI
# =============================================================================


def _print_summary(metrics: dict) -> None:
    def fmt(x: Optional[float]) -> str:
        return f"{x:.3f}" if x is not None else "n/a"

    for subset_name in ("all", "dev", "held_out"):
        m = metrics[subset_name]
        print(
            f"\n[{subset_name}] n={m['n_dialogues']}  "
            f"success={fmt(m['success_rate'])}  "
            f"citations={fmt(m['citation_correctness'])}  "
            f"structure={fmt(m['structure_adherence'])}  "
            f"refusal={fmt(m['refusal_rate'])}"
        )
        c = m["applicable_counts"]
        print(
            f"          applicable: success={c['success']} "
            f"cite={c['citation']} struct={c['structure']} "
            f"refusal={c['refusal']}"
        )


async def _run_all(
    runner: AgentRunner, dialogues: list[dict]
) -> list[DialogueScore]:
    scores = []
    for d in dialogues:
        result = await runner.run(d)
        scores.append(score_dialogue(d, result))
    return scores


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument(
        "--dataset", type=Path, default=DEFAULT_DATASET,
        help="JSONL c диалогами (default: datasets/e2e_dialogues.jsonl)",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Использовать MockRunner (default — GraphRunner; требует env)",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Не сохранять run в metrics/runs/ (для smoke-test)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Логировать per-dialogue результаты",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    dataset_path: Path = args.dataset
    if not dataset_path.exists():
        print(f"dataset not found: {dataset_path}", file=sys.stderr)
        return 2

    dialogues = load_dialogues(dataset_path)
    logger.info("loaded %d dialogues from %s", len(dialogues), dataset_path)

    runner: AgentRunner
    if args.mock:
        runner = MockRunner()
        runner_name = "mock"
    else:
        runner = GraphRunner()
        runner_name = "graph"

    scores = asyncio.run(_run_all(runner, dialogues))

    if args.verbose:
        for s in scores:
            print(json.dumps(asdict(s), ensure_ascii=False, indent=2))

    metrics = aggregate(scores)
    _print_summary(metrics)

    if not args.no_save:
        out_path = save_run(
            metrics, scores, runner_name=runner_name, dataset_path=dataset_path,
        )
        print(f"\nsaved: {out_path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
