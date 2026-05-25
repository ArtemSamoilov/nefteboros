"""Тесты advisory self-reflection (ADR-0029).

Покрывают: парсинг трейсов, детерминированные детекторы, LLM-пайплайн (stub),
dedup backlog'а, и — критично — safety-границы: НЕ auto-apply, изоляция от
analyst-пути, graceful при сбое LLM, AST-валидность. Это те проверки, что должны
убедить ревью: механизм РЕАЛЬНО работает и НЕ переписывает агента.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

from nefteboros.self_reflection import backlog as backlog_mod
from nefteboros.self_reflection import detectors, reflect, sources
from nefteboros.self_reflection.schema import ReflectionItem, TraceView


# ---------------------------------------------------------------------------
# Фикстуры трейсов
# ---------------------------------------------------------------------------


def _write_traces(path: pathlib.Path) -> None:
    """trace.jsonl с разным богатством: content-rich (с текстом ответа, как
    Langfuse/sample) и structural-only (как live JSONL-tracer)."""
    lines = [
        # 1) content-rich, с forecast-цитатой → has_citation, non-refusal
        {"kind": "trace", "ts": "2026-05-25T10:00:00+00:00", "trace_id": "t1",
         "query": "Прогноз Brent на 3 месяца?", "status": "ok",
         "total_latency_ms": 1200, "span_count": 2},
        {"kind": "span", "trace_id": "t1", "span_id": 1, "node": "classify_intent", "status": "ok"},
        {"kind": "span", "trace_id": "t1", "span_id": 2, "node": "forecast_call", "status": "ok"},
        {"kind": "span", "trace_id": "t1", "span_id": 3, "node": "validate_citations", "status": "ok"},
        {"kind": "span", "trace_id": "t1", "span_id": 4, "node": "synthesize", "status": "ok",
         "output": "Базовый сценарий: ~82 USD/барр [Forecast: ou_regime, scenario=base, CI 80%]."},
        # 2) refusal
        {"kind": "trace", "ts": "2026-05-25T10:01:00+00:00", "trace_id": "t2",
         "query": "Совет по акциям Apple?", "status": "ok", "total_latency_ms": 300},
        {"kind": "span", "trace_id": "t2", "span_id": 5, "node": "synthesize", "status": "ok",
         "output": "Запрос отклонён: вне доменной области нефтегазового анализа."},
        # 3) content-rich, БЕЗ цитат, non-refusal → no_citation flag
        {"kind": "trace", "ts": "2026-05-25T10:02:00+00:00", "trace_id": "t3",
         "query": "Что с рынком нефти?", "status": "ok", "total_latency_ms": 900},
        {"kind": "span", "trace_id": "t3", "span_id": 6, "node": "synthesize", "status": "ok",
         "output": "Рынок нефти стабилен, цены держатся в коридоре."},
        # 4) error trace
        {"kind": "trace", "ts": "2026-05-25T10:03:00+00:00", "trace_id": "t4",
         "query": "Прогноз на 5 лет?", "status": "error", "error_node": "forecast_call",
         "total_latency_ms": 12000},
        {"kind": "span", "trace_id": "t4", "span_id": 7, "node": "forecast_call",
         "status": "error", "error": {"type": "ValueError", "message": "horizon too long"}},
    ]
    with path.open("w", encoding="utf-8") as fh:
        for o in lines:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")


@pytest.fixture()
def traces_file(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "trace.jsonl"
    _write_traces(p)
    return p


# ---------------------------------------------------------------------------
# Источники / парсинг
# ---------------------------------------------------------------------------


def test_jsonl_source_parses_views(traces_file):
    views = sources.JsonlTraceSource(explicit_paths=[traces_file]).recent_traces(50)
    by_id = {v.trace_id: v for v in views}
    assert set(by_id) == {"t1", "t2", "t3", "t4"}

    t1 = by_id["t1"]
    assert t1.query == "Прогноз Brent на 3 месяца?"
    assert "Forecast: ou_regime" in (t1.answer or "")
    assert t1.has_answer_text
    assert "forecast_call" in t1.nodes and "validate_citations" in t1.nodes

    t4 = by_id["t4"]
    assert t4.status == "error"
    assert "forecast_call" in t4.error_nodes


def test_load_recent_traces_explicit_forces_jsonl(traces_file):
    views, source = sources.load_recent_traces(50, explicit_paths=[traces_file])
    assert source == "jsonl"
    assert len(views) == 4


# ---------------------------------------------------------------------------
# Детекторы (детерминированные сигналы)
# ---------------------------------------------------------------------------


def test_refusal_and_citation_helpers():
    assert detectors.looks_like_refusal("Запрос отклонён: вне доменной области")
    assert not detectors.looks_like_refusal("Базовый сценарий ~82 USD")
    assert detectors.has_citation("…[Forecast: ou_regime, scenario=base, CI 80%]")
    assert detectors.has_citation("…[OPEC MOMR март 2026, p.14]")
    assert not detectors.has_citation("Рынок нефти стабилен.")


def test_compute_signals_real_numbers(traces_file):
    views = sources.JsonlTraceSource(explicit_paths=[traces_file]).recent_traces(50)
    sig = detectors.compute_signals(views)
    assert sig.n_traces == 4
    assert sig.error_rate == pytest.approx(0.25)  # t4 ошибка из 4
    assert sig.error_node_counts.get("forecast_call") == 1
    # content-сигналы только по трейсам с текстом ответа: t1,t2,t3
    assert sig.n_with_answer == 3
    assert sig.refusal_rate == pytest.approx(1 / 3)  # t2
    assert sig.citation_rate == pytest.approx(1 / 3)  # только t1
    # структурные прокси: t3 — synthesize без инструмента и без validate_citations
    assert sig.tool_skip_count >= 1
    assert sig.citation_node_gap_count >= 1


def test_heuristic_items_are_grounded(traces_file):
    views = sources.JsonlTraceSource(explicit_paths=[traces_file]).recent_traces(50)
    sig = detectors.compute_signals(views)
    items = detectors.heuristic_items(sig)
    assert items, "должны быть heuristic-предложения при error_rate=25%"
    cats = {it.category for it in items}
    assert "error" in cats
    for it in items:
        assert it.source == "heuristic"
        assert it.observation and it.suggestion


# ---------------------------------------------------------------------------
# Backlog: dedup + applied всегда False
# ---------------------------------------------------------------------------


def test_backlog_append_dedup_and_advisory_only(tmp_path):
    bpath = tmp_path / "backlog.jsonl"
    items = [
        ReflectionItem(observation="Наблюдение A", suggestion="Сделать X",
                       severity="medium", category="error"),
        ReflectionItem(observation="Наблюдение B", suggestion="Сделать Y",
                       severity="low", category="citations"),
    ]
    assert backlog_mod.append_items(items, path=bpath) == 2
    # повторно — dedup по fingerprint
    assert backlog_mod.append_items(items, path=bpath) == 0

    entries = backlog_mod.load_entries(bpath)
    assert len(entries) == 2
    # safety: advisory — ничего не применено
    assert all(e["applied"] is False for e in entries)
    assert all(e["status"] == "open" for e in entries)
    assert all(e["id"].startswith("sr-") for e in entries)


def test_backlog_skips_empty_items(tmp_path):
    bpath = tmp_path / "backlog.jsonl"
    items = [ReflectionItem(observation="", suggestion="X"),
             ReflectionItem(observation="Y", suggestion="")]
    assert backlog_mod.append_items(items, path=bpath) == 0


# ---------------------------------------------------------------------------
# LLM-пайплайн (stub) — РЕАЛЬНЫЙ путь, замокан только транспорт
# ---------------------------------------------------------------------------


def _stub_chat_returning(items_json):
    def fake_chat(self, messages, model, **kwargs):  # noqa: ANN001
        return {"content": json.dumps(items_json, ensure_ascii=False)}, {"cost": 0.0}
    return fake_chat


def test_run_reflection_with_llm_stub_writes_backlog(traces_file, tmp_path, monkeypatch):
    import ouroboros.llm as llm_mod

    llm_items = [
        {"observation": "Агент часто отвечает без цитат (t3).",
         "suggestion": "Усилить требование цитирования в synthesize.",
         "severity": "medium", "category": "citations", "evidence_trace_id": "t3"},
        {"observation": "forecast_call падает на длинном горизонте.",
         "suggestion": "Возвращать явный отказ при horizon>18m, не исключение.",
         "severity": "high", "category": "error", "evidence_trace_id": "t4"},
    ]
    monkeypatch.setattr(llm_mod.LLMClient, "chat", _stub_chat_returning(llm_items))
    monkeypatch.setenv("OUROBOROS_REFLECTION_MODEL", "anthropic/claude-sonnet-4.6")

    bpath = tmp_path / "backlog.jsonl"
    result = reflect.run_reflection(
        explicit_paths=[traces_file], use_llm=True, backlog_path=bpath
    )
    assert result.source == "jsonl"
    assert result.llm_used is True
    assert result.n_traces == 4
    assert len(result.items) == 2
    assert all(it.source == "llm" for it in result.items)
    assert result.added == 2

    entries = backlog_mod.load_entries(bpath)
    assert {e["category"] for e in entries} == {"citations", "error"}
    assert all(e["applied"] is False for e in entries)  # safety


def test_run_reflection_graceful_when_llm_raises(traces_file, tmp_path, monkeypatch):
    """Сбой LLM НЕ роняет пайплайн — graceful откат на heuristic floor."""
    import ouroboros.llm as llm_mod

    def boom(self, messages, model, **kwargs):  # noqa: ANN001
        raise RuntimeError("API unavailable")

    monkeypatch.setattr(llm_mod.LLMClient, "chat", boom)
    monkeypatch.setenv("OUROBOROS_REFLECTION_MODEL", "anthropic/claude-sonnet-4.6")

    bpath = tmp_path / "backlog.jsonl"
    result = reflect.run_reflection(
        explicit_paths=[traces_file], use_llm=True, backlog_path=bpath
    )
    assert result.llm_used is False
    assert "fallback" in result.note.lower()
    assert result.items, "heuristic floor должен дать предложения"
    assert all(it.source == "heuristic" for it in result.items)
    assert result.added == len(result.items)


def test_parse_items_extracts_json_from_prose():
    content = (
        "Вот мой анализ паттернов.\n\n"
        '[{"observation":"O","suggestion":"S","severity":"low","category":"latency"}]\n'
        "Готово."
    )
    items = reflect._parse_items(content)
    assert len(items) == 1
    assert items[0].category == "latency"
    assert items[0].source == "llm"


def test_parse_items_handles_garbage():
    assert reflect._parse_items("no json here") == []
    assert reflect._parse_items("") == []


# ---------------------------------------------------------------------------
# SAFETY: изоляция и отсутствие auto-apply (критичные проверки ADR-0029)
# ---------------------------------------------------------------------------

_PKG_DIR = REPO / "nefteboros" / "self_reflection"
_PKG_FILES = sorted(_PKG_DIR.glob("*.py")) + [REPO / "scripts" / "self_reflect.py"]

# Модули analyst-пути / истории чата, которых пакет НЕ должен ИМПОРТИРОВАТЬ
# (ADR-0027). Проверяем по AST (реальные import'ы), а не grep'ом по тексту —
# иначе ловим собственные docstring'и, описывающие границу.
_FORBIDDEN_IMPORT_PREFIXES = (
    "nefteboros.graphs",
    "ouroboros.context",
    "ouroboros.agent",
    "ouroboros.consolidator",
)


def _imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    return mods


def _non_docstring_strings(tree: ast.AST) -> list[str]:
    """Все строковые литералы КРОМЕ docstring'ов (чтобы прозой про границу не
    фейлить проверки на код)."""
    doc_ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_ids.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids
    ]


def _sets_applied_true(tree: ast.AST) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.keyword) and n.arg == "applied" \
                and isinstance(n.value, ast.Constant) and n.value.value is True:
            return True
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value is True:
            for t in n.targets:
                nm = t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", None)
                if nm == "applied":
                    return True
    return False


def test_new_files_are_valid_python():
    for f in _PKG_FILES:
        ast.parse(f.read_text(encoding="utf-8"))


def test_reflection_does_not_import_analyst_path():
    """Пакет не импортирует analyst-граф/контекст/agent/консолидатор — изоляция
    по ADR-0027 (проверка по AST-импортам)."""
    for f in _PKG_FILES:
        mods = _imported_modules(ast.parse(f.read_text(encoding="utf-8")))
        for m in mods:
            for bad in _FORBIDDEN_IMPORT_PREFIXES:
                assert not m.startswith(bad), f"{f.name} импортирует запрещённое '{m}'"


def test_reflection_does_not_read_chat_jsonl():
    """Никакой строковый литерал в КОДЕ (не docstring) не ссылается на
    chat.jsonl — рефлексия не читает историю чата (ADR-0027)."""
    for f in _PKG_FILES:
        for s in _non_docstring_strings(ast.parse(f.read_text(encoding="utf-8"))):
            assert "chat.jsonl" not in s, f"{f.name}: строковый литерал ссылается на chat.jsonl"


def test_prod_does_not_depend_on_self_reflection():
    """Ни прод-код агента (server.py, ouroboros/*, nefteboros/graphs/*,
    nefteboros/observability/*), ни что-либо ещё не импортирует self_reflection
    и не читает backlog обратно в контекст. Разрешено ТОЛЬКО: сам пакет, CLI,
    тесты."""
    roots = [REPO / "server.py", REPO / "ouroboros",
             REPO / "nefteboros" / "graphs", REPO / "nefteboros" / "observability"]
    offenders = []
    for root in roots:
        files = [root] if root.is_file() else list(root.rglob("*.py"))
        for f in files:
            try:
                src = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if "self_reflection" in src:
                offenders.append(str(f.relative_to(REPO)))
    assert not offenders, (
        "self_reflection не должен импортироваться прод-кодом агента: " + ", ".join(offenders)
    )


def test_no_code_path_sets_applied_true():
    """Гарантия отсутствия auto-apply: нигде в пакете код не выставляет
    applied=True (проверка по AST — keyword/assign, не по тексту docstring'ов)."""
    for f in _PKG_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        assert not _sets_applied_true(tree), f"{f.name}: код выставляет applied=True"


def test_env_flag_default_off(monkeypatch):
    import nefteboros.self_reflection as sr

    monkeypatch.delenv(sr.ENV_FLAG, raising=False)
    assert sr.is_enabled() is False
    monkeypatch.setenv(sr.ENV_FLAG, "1")
    assert sr.is_enabled() is True
