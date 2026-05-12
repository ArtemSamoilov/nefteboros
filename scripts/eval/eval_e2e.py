#!/usr/bin/env python3
"""End-to-end eval на golden dialogues.

E2E задача — оценить **финальный итоговый результат** агента, не лезть
в глубину тулов. Сверка чисел RAG-цитат и сверка соответствия web-результатов
делается на стороне тулов (см. ``eval_citations.py``, ``eval_rag.py``,
``eval_forecast.py``). Здесь измеряется только то, что видит пользователь:
финальный текст и факт вызова тулов.

Метрики (см. roadmap-v2.1 Track D):

1. **success_rate** — non-refusal-expected диалоги где агент дал
   нон-empty ответ без явного «нет данных» / «не знаю» и попадает в
   половину или более ожидаемых ключевых слов.
2. **citation_correctness** — non-refusal где (a) общее число цитат в
   формате RAG/Web/Forecast ≥ ``expected_min_citations``, (b) если
   ``should_use_<tool>=true`` — соответствующая цитата в формате
   присутствует в ответе. Семантическая сверка с источниками — задача
   ``eval_citations.py``, не e2e.
3. **structure_adherence** — non-refusal где ``check_structure().passed``
   (TL;DR + числовой факт + цитата).
4. **refusal_rate** — refusal-expected где агент корректно отказался
   (явный refused-флаг или короткий ответ без tools).

Runner:

- ``--mock`` — :class:`MockRunner` с шаблонами по scenario_type. Smoke
  без LLM-ключей; проверяет что eval-код считает метрики корректно.
- ``--ws`` — :class:`WSRunner` через WebSocket к ``server.py`` (default).
  **Все запросы попадают в Langfuse** через handle_task wrap (Track F):
  каждый dialogue → root user_request trace + child observations
  (classify_intent, forecast_call, synthesize, web_search, rag_search,
  validate_citations). Это unified observability — eval test set и
  production user requests наблюдаются одинаково. Требует running
  server.py на ``EVAL_WS_URL`` (default ws://localhost:8000/ws).
- ``--graph`` — legacy :class:`GraphRunner` через прямой
  ``analyst_graph.ainvoke()``. **Не пишет в Langfuse** (нет handle_task
  wrap, observations попадают как orphan top-level traces). Покрывает
  только forecast flow, RAG/web tools уйдут в out_of_scope. Оставлен
  для unit-test ситуаций где server недоступен.

Use:

    python -m scripts.eval.eval_e2e --mock                 # smoke без LLM
    python -m scripts.eval.eval_e2e                        # ws (default)
    python -m scripts.eval.eval_e2e --graph                # legacy direct
    python -m scripts.eval.eval_e2e --limit 5              # subset
    python -m scripts.eval.eval_e2e --dataset path.jsonl   # custom

Output:

    metrics/runs/<UTC-timestamp>_e2e_<runner>_<commit>.json
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
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "datasets" / "e2e_dialogues.jsonl"
METRICS_RUNS_DIR = REPO_ROOT / "metrics" / "runs"


def _bootstrap_env() -> None:
    """Загрузить .env (текущий worktree → parent repo → cwd) и заполнить
    OPENAI_COMPATIBLE_* из HYDRA_*, если первые не заданы.

    Worktree обычно без своей .env — лежит в parent. Synthesize node
    использует ``OPENAI_COMPATIBLE_BASE_URL/API_KEY`` (default префикс
    модели — ``openai-compatible::kimi-k2p6``); в .env у нас
    ``HYDRA_API_KEY`` + ``HYDRA_BASE_URL``. Auto-mapping избавляет от
    ручных export перед каждым запуском.
    """
    import os

    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
    except ImportError:
        return

    # Поднимаемся по дереву от REPO_ROOT до filesystem root — worktree
    # лежит в .claude/worktrees/<name>/, а реальный .env у parent-репо
    # ``/Users/.../nefteboros/.env`` (3 уровня вверх).
    candidates: list[Path] = []
    cur = REPO_ROOT
    for _ in range(6):
        candidates.append(cur / ".env")
        if cur.parent == cur:
            break
        cur = cur.parent
    candidates.append(Path.cwd() / ".env")
    for p in candidates:
        if p.exists():
            load_dotenv(p, override=False)
            logger.info("loaded env from %s", p)
            break
    else:
        logger.warning("no .env found in candidates: %s", candidates)

    if not os.environ.get("OPENAI_COMPATIBLE_API_KEY") and os.environ.get("HYDRA_API_KEY"):
        os.environ["OPENAI_COMPATIBLE_API_KEY"] = os.environ["HYDRA_API_KEY"]
    if not os.environ.get("OPENAI_COMPATIBLE_BASE_URL"):
        os.environ["OPENAI_COMPATIBLE_BASE_URL"] = os.environ.get(
            "HYDRA_BASE_URL", "https://hydragpt.ru/v1",
        )


# =============================================================================
# Run result + per-dialogue score
# =============================================================================


@dataclass
class RunResult:
    """Финальный результат одного прогона диалога runner'ом.

    Только то, что видит пользователь + факт вызова тулов. Никаких
    retrieved_chunks / web_results / forecast_calls — e2e не лезет в
    tool internals.
    """

    answer: str
    tools_called: list[str] = field(default_factory=list)
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
    rag_citations: int = 0
    web_citations: int = 0
    forecast_citations: int = 0
    error: Optional[str] = None


# =============================================================================
# Runners
# =============================================================================


class AgentRunner(Protocol):
    async def run(self, dialogue: dict) -> RunResult: ...


class MockRunner:
    """Шаблонные ответы по scenario_type для smoke / sanity.

    Не отражает качество настоящего агента — задача только в проверке
    что pipeline и метрики считаются корректно. Для realistic baseline
    нужен GraphRunner с реальным LLM.
    """

    _MOCK_BY_SCENARIO: dict[str, dict] = {
        "rag_only": {
            "answer": (
                "OPEC MOMR март 2026 фиксирует снижение квот на 1.4 mbpd.\n\n"
                "Ключевые тезисы [OPEC MOMR март 2026, p.14]: квоты Q2 2026, "
                "продление режима до конца года."
            ),
            "tools_called": ["rag_search"],
            "refused": False,
        },
        "web_only": {
            "answer": (
                "Brent торгуется около $82.5/bbl на momentum от заявлений ОПЕК+.\n\n"
                "Свежее: [Brent above $82](https://reuters.com/article/x) — reuters.com, web."
            ),
            "tools_called": ["web_search"],
            "refused": False,
        },
        "rag_plus_web": {
            "answer": (
                "Спрос вырастет на 1.5 mbpd по OPEC, динамика подтверждена СМИ.\n\n"
                "По [OPEC MOMR март 2026, p.10] спрос +1.5 mbpd; "
                "[ОПЕК+ продлил квоты](https://reuters.com/x) — reuters.com, web."
            ),
            "tools_called": ["rag_search", "web_search"],
            "refused": False,
        },
        "forecast": {
            "answer": (
                "Brent на 6 месяцев: $80-$88, центр $84.\n\n"
                "Прогноз [Forecast: ensemble, CI 80%]; основной риск — решение ОПЕК+."
            ),
            "tools_called": ["forecast"],
            "refused": False,
        },
        "out_of_scope": {
            "answer": (
                "Этот вопрос вне моей компетенции — отвечаю по нефтегазу. "
                "Рекомендую профильный источник."
            ),
            "tools_called": [],
            "refused": True,
        },
        "multi_tool": {
            "answer": (
                "Санкции 2025 увеличили Urals discount до $15-$18/bbl.\n\n"
                "Контекст: [Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap, p.7]. "
                "Прогноз Urals на 6m: $63-$68 [Forecast: ensemble, CI 80%]."
            ),
            "tools_called": ["rag_search", "forecast"],
            "refused": False,
        },
        "follow_up": {
            "answer": (
                "При снижении квот ОПЕК+ на 1 mbpd Brent сместится к $90-$95.\n\n"
                "Корректировка к baseline'у [Forecast: ensemble, CI 80%]; "
                "источник правил квот [OPEC MOMR март 2026, p.14]."
            ),
            "tools_called": ["forecast", "rag_search"],
            "refused": False,
        },
        "unknown_with_hypothesis": {
            "answer": (
                "Снятие санкций с Ирана сократит Urals discount до $5-$10/bbl, гипотеза.\n\n"
                "По [CRS — U.S. Conflict with Iran (March 26, 2026), p.5] возврат 1.5 mbpd "
                "иранской нефти давит на спред; точечная цифра — сценарная неопределённость."
            ),
            "tools_called": ["rag_search"],
            "refused": False,
        },
        "adversarial": {
            "answer": (
                "Не могу дать точечный прогноз без CI — это снижает аналитическую ценность.\n\n"
                "Brent на 3m: $80-$87, центр $83 [Forecast: ensemble, CI 80%]; "
                "основной риск — решение ОПЕК+."
            ),
            "tools_called": ["forecast"],
            "refused": False,
        },
    }

    async def run(self, dialogue: dict) -> RunResult:
        scenario = dialogue.get("scenario_type", "rag_only")
        mock = self._MOCK_BY_SCENARIO.get(scenario, self._MOCK_BY_SCENARIO["rag_only"])
        return RunResult(
            answer=mock["answer"],
            tools_called=list(mock.get("tools_called", [])),
            refused=mock.get("refused", False),
        )


class WSRunner:
    """Real e2e runner через WebSocket к ``server.py``.

    Каждый dialogue проходит через **полный Ouroboros loop**:
    ``WS chat`` → ``handle_task`` (root span ``user_request``) →
    ``analyst_query`` skill (если intent forecast) → tool-loop с
    web_search / rag_search / forecast_call / classify_intent /
    synthesize — все с @observe-spans.

    **Зачем именно WS, а не direct graph call:**

    1. **Unified observability** — Track F observability работает только
       внутри handle_task. Direct ``graph.ainvoke()`` (см. GraphRunner)
       теряет TraceContext, observations попадают в Langfuse как
       orphan top-level traces без parent → невозможно correlate.
       WS-pipeline даёт каждому dialogue **один root user_request trace**
       с детальной иерархией observations.

    2. **Production-parity** — продовый user пишет через WS chat (browser).
       Если eval идёт тем же путём — measured качество = реальное.

    3. **Tools coverage** — analyst_graph (direct) покрывает только
       forecast. RAG/web tools — на уровне Ouroboros tool-loop. Только
       WS даёт полное multi-tool покрытие.

    **Ограничения**:
    - Требует running ``server.py`` (default ``ws://localhost:8000/ws``).
      Health-check рекомендуется до старта.
    - Sequential по диалогам (один WS connection = один dialogue) —
      параллелизм отложен (rate limits LLM провайдера).
    - ``tools_called`` извлекается best-effort из Langfuse API после
      завершения dialogue (отдельная сетевая ходка ~1-2s). Если
      LANGFUSE_* env не задан — ``tools_called`` остаётся пустым.

    Per-dialogue session_id формируется как ``eval:{dialogue_id}_{ts}``
    чтобы traces разных диалогов не пересекались (critical для readback).
    """

    def __init__(
        self,
        *,
        server_url: Optional[str] = None,
        timeout_seconds: float = 360.0,
    ) -> None:
        self._url = server_url or os.environ.get(
            "EVAL_WS_URL", "ws://localhost:8000/ws"
        )
        self._timeout = timeout_seconds

    async def run(self, dialogue: dict) -> RunResult:
        import time as _time

        try:
            import websockets  # noqa: F401  — runtime check
        except ImportError:
            return RunResult(
                answer="",
                error="websockets package not installed (pip install websockets)",
            )
        import websockets

        user_messages = [
            m for m in dialogue["messages"] if m.get("role") == "user"
        ]
        if not user_messages:
            return RunResult(answer="", error="no user messages in dialogue")
        query = user_messages[-1]["content"]

        dialogue_id = dialogue.get("id", "unknown")
        ts = int(_time.time())
        session_id = f"eval:{dialogue_id}_{ts}"
        msg_id = f"eval_msg_{dialogue_id}_{ts}"

        last_content = ""
        try:
            async with websockets.connect(
                self._url, max_size=10 * 1024 * 1024, open_timeout=10
            ) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "chat",
                            "content": query,
                            "sender_session_id": session_id,
                            "client_message_id": msg_id,
                        }
                    )
                )
                # Break logic. Не доверяем "первому substantial chunk":
                # для tool-call paths agent loop сначала шлёт notification
                # chunk (~85 chars, "⚡ Fallback ..."), затем долго работает
                # (rag/web/forecast tools, ~20-60s), и только потом — финальный
                # ответ. Раннее break теряет реальный ответ.
                #
                # Стратегия: ждать любого из 3 сигналов:
                # 1. `done=True` flag на assistant chunk (явный finish).
                # 2. Log event `task_metrics_event` для нашего task — это
                #    server-side сигнал, что agent loop завершён.
                # 3. Idle timeout: ``IDLE_TIMEOUT`` секунд без новых assistant
                #    chunks ПОСЛЕ того как хотя бы один chunk был получен.
                IDLE_TIMEOUT_S = 45.0
                t0 = _time.time()
                last_chunk_ts: Optional[float] = None
                last_chars = 0
                task_finished = False
                while _time.time() - t0 < self._timeout:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    except asyncio.TimeoutError:
                        # Idle check: если уже был chunk и тишина > IDLE.
                        if (
                            last_chunk_ts is not None
                            and _time.time() - last_chunk_ts > IDLE_TIMEOUT_S
                        ):
                            break
                        continue
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    mtype = msg.get("type")
                    if mtype == "chat" and msg.get("role") == "assistant":
                        content = msg.get("content", "") or ""
                        if content and len(content) != last_chars:
                            last_content = content
                            last_chars = len(content)
                            last_chunk_ts = _time.time()
                        if msg.get("done") and content:
                            # Грейс на хвост log-events.
                            await asyncio.sleep(1.0)
                            break
                    elif mtype == "log":
                        # Server emits `task_metrics_event` когда agent loop
                        # завершён (см. ouroboros agent_task_pipeline).
                        data = msg.get("data") or {}
                        if isinstance(data, dict) and data.get("type") == (
                            "task_metrics_event"
                        ):
                            task_finished = True
                            # Грейс 2с — финальный assistant chunk обычно
                            # приходит до task_metrics_event, но иногда
                            # сразу после.
                            await asyncio.sleep(2.0)
                            break
                else:
                    return RunResult(
                        answer=last_content,
                        error=f"timeout > {self._timeout}s",
                    )
        except Exception as exc:  # noqa: BLE001 — runner не должен падать
            return RunResult(
                answer=last_content,
                error=f"{type(exc).__name__}: {exc}",
            )

        # tools_called — выводим из citations в answer (определённо).
        # Альтернатива: readback из Langfuse через session_id — сделал бы
        # eval медленнее (8s ingest sleep × N) и зависимым от cloud
        # availability. Citation-based вывод детерминирован и достаточен
        # для scoring (`citation_correctness` всё равно проверяет именно
        # наличие правильного типа цитаты в ответе).
        tools_called = self._tools_from_answer(last_content)

        # refused — heuristic по содержимому. Точечная классификация
        # делается scoring-ом (см. score_dialogue), здесь — флаг для metrics.
        refused = bool(last_content) and any(
            phrase in last_content.lower()
            for phrase in (
                "запрос отклонён",
                "запрос не покрыт",
                "вне доменной",
                "не в моей компетенции",
                "запрос отклонен",
            )
        )

        return RunResult(
            answer=last_content,
            tools_called=tools_called,
            refused=refused,
        )

    @staticmethod
    def _tools_from_answer(answer: str) -> list[str]:
        """Извлечь tools_called из citations в финальном тексте.

        Mapping: наличие RAG/Web/Forecast-цитаты → соответствующий tool.
        Использует те же паттерны что и scoring (`nefteboros.citations`).
        """
        if not answer:
            return []
        try:
            from nefteboros.citations import (
                parse_forecast_citations,
                parse_rag_citations,
                parse_web_citations,
            )
        except ImportError:
            return []
        tools: list[str] = []
        if any(parse_rag_citations(answer)):
            tools.append("rag_search")
        if any(parse_web_citations(answer)):
            tools.append("web_search")
        if any(parse_forecast_citations(answer)):
            tools.append("forecast")
        return tools


class GraphRunner:
    """Реальный runner через ``analyst_graph.build_analyst_graph()``.

    Известное ограничение v2.0.0: ``analyst_graph`` — minimal graph (см.
    ADR-0014), он покрывает только **forecast** flow и refusal'ы. RAG и
    Web tools зарегистрированы как skill, но вызываются на уровне
    Ouroboros tool-loop'а, не внутри LangGraph. Для real e2e на не-forecast
    диалогах нужен полный Ouroboros loop (отдельный harness через
    HTTP API ``server.py``).

    На текущем GraphRunner покрытие:
    - forecast / out_of_scope / russian_gas_refusal — реальный agent run
    - rag_only / web_only / multi_tool / follow_up / unknown_with_hypothesis
      / adversarial — agent уйдёт в out_of_scope (нет RAG/web в графе),
      это **сама по себе** реальная находка.
    """

    def __init__(self, *, timeout_seconds: float = 90.0) -> None:
        self._timeout = timeout_seconds
        self._graph = None  # lazy build

    def _ensure_graph(self):
        if self._graph is None:
            from nefteboros.graphs.analyst_graph import build_analyst_graph
            self._graph = build_analyst_graph()
        return self._graph

    async def run(self, dialogue: dict) -> RunResult:
        from nefteboros.graphs.state import GraphState, IntentType

        # Используем последнее user сообщение из диалога. Multi-turn
        # full-context support — отдельная задача (analyst_graph принимает
        # только query, не messages history).
        user_messages = [m for m in dialogue["messages"] if m.get("role") == "user"]
        if not user_messages:
            return RunResult(answer="", error="no user messages in dialogue")
        query = user_messages[-1]["content"]

        try:
            graph = self._ensure_graph()
            final = await asyncio.wait_for(
                graph.ainvoke(GraphState(query=query)),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            return RunResult(answer="", error=f"timeout > {self._timeout}s")
        except Exception as e:  # noqa: BLE001 — runner не должен падать
            return RunResult(answer="", error=f"{type(e).__name__}: {e}")

        # Извлекаем что есть в state
        synthesis = final.get("synthesis", "") or ""
        intent = final.get("intent")
        forecast_results = final.get("forecast_results") or []

        # tools_called: analyst_graph покрывает только forecast.
        tools_called: list[str] = []
        if forecast_results:
            tools_called.append("forecast")
        # rag_search / web_search — НЕ В ГРАФЕ. Реальный baseline покажет
        # что RAG-only / Web-only диалоги уйдут в out_of_scope — это
        # ожидаемое (для v2.0.0 minimal-graph) поведение.

        # refused: graph выдаёт refusal через intent type
        refused = False
        if intent is not None:
            intent_type = getattr(intent, "type", None)
            if intent_type in (IntentType.OUT_OF_SCOPE, IntentType.RUSSIAN_GAS_REFUSAL):
                refused = True

        return RunResult(
            answer=synthesis,
            tools_called=tools_called,
            refused=refused,
        )


# =============================================================================
# Scoring
# =============================================================================


_NEGATIVE_REFUSAL_RE = re.compile(
    r"(нет\s+данн|не\s+знаю|не\s+нашёл|информации\s+нет|данных\s+нет)",
    re.IGNORECASE,
)


def _matches_keyword(answer: str, keyword: str) -> bool:
    return keyword.lower() in answer.lower()


def _is_negative_refusal(answer: str) -> bool:
    return bool(_NEGATIVE_REFUSAL_RE.search(answer))


def score_dialogue(dialogue: dict, result: RunResult) -> DialogueScore:
    """Считает per-метрики для одного диалога.

    Цитаты считаются **только по формату** — сверка с источниками не
    задача e2e (см. eval_citations.py). Здесь интересует:
    1) есть ли цитата в формате,
    2) правильный ли тип формата для ожидаемого tool'а,
    3) общее число ≥ expected_min.
    """
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

    # Импорт внутри — на случай тестирования из изменённого PYTHONPATH'а.
    from nefteboros.citations import (
        parse_forecast_citations,
        parse_rag_citations,
        parse_web_citations,
    )
    from scripts.eval.structure import check_structure

    keywords_total = len(expected.get("expected_keywords", []))
    keywords_matched = sum(
        1 for kw in expected.get("expected_keywords", [])
        if _matches_keyword(result.answer, kw)
    )

    # Подсчёт цитат по формату (без сверки с источниками)
    rag_cites = list(parse_rag_citations(result.answer))
    web_cites = list(parse_web_citations(result.answer))
    forecast_cites = list(parse_forecast_citations(result.answer))
    total_citations = len(rag_cites) + len(web_cites) + len(forecast_cites)

    if expected_refusal:
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
            citation_count=total_citations,
            rag_citations=len(rag_cites),
            web_citations=len(web_cites),
            forecast_citations=len(forecast_cites),
        )

    # Non-refusal: оцениваем все три метрики
    success = (
        len(result.answer.strip()) > 0
        and not _is_negative_refusal(result.answer)
        and (keywords_matched >= max(1, keywords_total // 2) if keywords_total else True)
    )

    # Citation correctness в e2e:
    # 1. count >= expected_min
    # 2. tool selection match: если ожидали RAG/web/forecast — соответствующий
    #    формат должен быть в ответе.
    expected_min = int(expected.get("expected_min_citations", 0))
    expects_rag = bool(expected.get("should_use_rag", False))
    expects_web = bool(expected.get("should_use_web", False))
    expects_forecast = bool(expected.get("should_call_forecast", False))

    rag_match = (not expects_rag) or len(rag_cites) >= 1
    web_match = (not expects_web) or len(web_cites) >= 1
    forecast_match = (not expects_forecast) or len(forecast_cites) >= 1

    citation_correct = (
        total_citations >= expected_min
        and rag_match
        and web_match
        and forecast_match
    )

    structure_report = check_structure(result.answer)

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
        citation_count=total_citations,
        rag_citations=len(rag_cites),
        web_citations=len(web_cites),
        forecast_citations=len(forecast_cites),
    )


# =============================================================================
# Aggregation
# =============================================================================


def _safe_div(num: int, den: int) -> Optional[float]:
    return num / den if den else None


def aggregate(scores: list[DialogueScore]) -> dict:
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

    # Per-scenario breakdown — для нахождения проблемных категорий
    scenario_groups: dict[str, list[DialogueScore]] = {}
    for s in scores:
        scenario_groups.setdefault(s.scenario_type, []).append(s)

    return {
        "all": metrics_for(scores),
        "dev": metrics_for([s for s in scores if not s.held_out]),
        "held_out": metrics_for([s for s in scores if s.held_out]),
        "by_scenario": {
            scenario: metrics_for(group)
            for scenario, group in sorted(scenario_groups.items())
        },
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
    partial_done: Optional[int] = None,
) -> Path:
    METRICS_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    commit = _git_short_commit()
    suffix = f"_partial_{partial_done:03d}" if partial_done is not None else ""
    out_path = METRICS_RUNS_DIR / f"{timestamp}_e2e_{runner_name}_{commit}{suffix}.json"
    # `relative_to` падает с ValueError, если dataset вне REPO_ROOT
    # (subset из /tmp, custom path); fallback на absolute string.
    try:
        dataset_str = str(dataset_path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        dataset_str = str(dataset_path.resolve())
    payload = {
        "timestamp_utc": timestamp,
        "git_commit": commit,
        "runner": runner_name,
        "dataset": dataset_str,
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

    print("\n[by scenario]")
    for scenario, m in metrics["by_scenario"].items():
        print(
            f"  {scenario:30s} n={m['n_dialogues']:2d}  "
            f"success={fmt(m['success_rate'])}  "
            f"cite={fmt(m['citation_correctness'])}  "
            f"struct={fmt(m['structure_adherence'])}  "
            f"refusal={fmt(m['refusal_rate'])}"
        )


async def _run_all(
    runner: AgentRunner,
    dialogues: list[dict],
    *,
    checkpoint_cb=None,
    checkpoint_every: int = 10,
) -> list[DialogueScore]:
    """Sequential runner с inter-dialogue паузой.

    Inter-dialogue sleep (3s) — workaround для async Langfuse SDK flush.
    Короткие диалоги (refusal ~20s, web_only ~60s) возвращают handle_task
    раньше, чем background batch успевает отправить trace. Без паузы
    следующий dialogue стартует <1s после → next root span overlap →
    batch drops старый trace. С паузой 3s — flush успевает (interval
    Langfuse SDK ~1s + network).

    ``checkpoint_cb(scores, done)`` вызывается каждые ``checkpoint_every``
    диалогов (не на последнем — финальный save делает main). Защита от
    потери метрик если процесс убит в середине прогона.
    """
    INTER_DIALOGUE_SLEEP_S = 3.0
    scores = []
    for i, d in enumerate(dialogues):
        result = await runner.run(d)
        scores.append(score_dialogue(d, result))
        done = i + 1
        if (
            checkpoint_cb is not None
            and done % checkpoint_every == 0
            and done < len(dialogues)
        ):
            try:
                checkpoint_cb(list(scores), done)
            except Exception:
                logger.exception("checkpoint_cb failed at %d", done)
        # Не sleep после последнего.
        if i < len(dialogues) - 1:
            await asyncio.sleep(INTER_DIALOGUE_SLEEP_S)
    return scores


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument(
        "--dataset", type=Path, default=DEFAULT_DATASET,
        help="JSONL c диалогами (default: datasets/e2e_dialogues.jsonl)",
    )
    runner_group = parser.add_mutually_exclusive_group()
    runner_group.add_argument(
        "--mock", action="store_true",
        help="MockRunner (шаблоны, без LLM/server). Smoke metric-кода.",
    )
    runner_group.add_argument(
        "--graph", action="store_true",
        help=(
            "Legacy GraphRunner (direct analyst_graph.ainvoke). "
            "Не пишет user_request traces в Langfuse — observations "
            "попадают как orphan top-level. Только для unit-тестов "
            "без running server'а."
        ),
    )
    parser.add_argument(
        "--ws-url", type=str, default=None,
        help=(
            "WS endpoint для WSRunner. Default: $EVAL_WS_URL или "
            "ws://localhost:8000/ws."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Прогнать только первые N диалогов (для smoke / partial baseline).",
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
    if args.limit is not None and args.limit > 0:
        dialogues = dialogues[: args.limit]
    logger.info("loaded %d dialogues from %s", len(dialogues), dataset_path)

    runner: AgentRunner
    if args.mock:
        runner = MockRunner()
        runner_name = "mock"
    elif args.graph:
        _bootstrap_env()
        runner = GraphRunner()
        runner_name = "graph"
    else:
        # default — WSRunner: unified observability через server.py
        _bootstrap_env()
        runner = WSRunner(server_url=args.ws_url)
        runner_name = "ws"

    def _checkpoint(scores_snap: list[DialogueScore], done: int) -> None:
        if args.no_save:
            return
        partial_metrics = aggregate(scores_snap)
        save_run(
            partial_metrics,
            scores_snap,
            runner_name=runner_name,
            dataset_path=dataset_path,
            partial_done=done,
        )
        m = partial_metrics["all"]
        print(
            f"[checkpoint {done}/{len(dialogues)}] "
            f"success={m['success_rate']} "
            f"cite={m['citation_correctness']} "
            f"struct={m['structure_adherence']} "
            f"refusal={m['refusal_rate']}",
            flush=True,
        )

    scores = asyncio.run(
        _run_all(runner, dialogues, checkpoint_cb=_checkpoint, checkpoint_every=10)
    )

    if args.verbose:
        for s in scores:
            print(json.dumps(asdict(s), ensure_ascii=False, indent=2))

    metrics = aggregate(scores)
    _print_summary(metrics)

    if not args.no_save:
        out_path = save_run(
            metrics, scores, runner_name=runner_name, dataset_path=dataset_path,
        )
        try:
            display_path = out_path.relative_to(REPO_ROOT)
        except ValueError:
            display_path = out_path
        print(f"\nsaved: {display_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
