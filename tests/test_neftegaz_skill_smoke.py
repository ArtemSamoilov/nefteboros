"""Smoke-тесты skill `neftegaz_analyst`.

См. ADR-0016. Тестируется:

- Manifest валидный, без warnings (frontmatter parse).
- discover_skills видит skill в bundled директории.
- Permissions ровно [tool, route].
- type=extension, entry=plugin.py.
- register(api) через capture-mock регистрирует analyst_query + health.
- _tool_analyst_query: пустой / слишком длинный query → error JSON.
- _tool_analyst_query: happy path с monkey-patched build_analyst_graph.

Heavy stack (LangGraph + langchain-gigachat + forecast()) НЕ импортируется —
skill использует lazy import графа в handler'е.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "neftegaz_analyst"


# =============================================================================
# Manifest
# =============================================================================


def test_manifest_parses_without_warnings() -> None:
    """SKILL.md frontmatter валидный по skill_manifest schema_version=1."""
    from ouroboros.contracts.skill_manifest import parse_skill_manifest_text

    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    manifest = parse_skill_manifest_text(text)

    warnings = manifest.validate()
    assert warnings == [], f"manifest validation warnings: {warnings}"

    assert manifest.name == "neftegaz_analyst"
    assert manifest.type == "extension"
    assert manifest.entry == "plugin.py"
    assert manifest.is_extension()


def test_manifest_permissions_minimal() -> None:
    """Permissions ровно [tool, route] — никаких лишних."""
    from ouroboros.contracts.skill_manifest import parse_skill_manifest_text

    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    manifest = parse_skill_manifest_text(text)

    assert sorted(manifest.permissions) == ["route", "tool"]


def test_manifest_no_env_from_settings() -> None:
    """env_from_settings пуст — skill не запрашивает Ouroboros core settings."""
    from ouroboros.contracts.skill_manifest import parse_skill_manifest_text

    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    manifest = parse_skill_manifest_text(text)

    assert manifest.env_from_settings == []


# =============================================================================
# discover_skills (bundled)
# =============================================================================


def test_discover_skills_finds_neftegaz_analyst(tmp_path) -> None:
    """discover_skills с repo_path = nefteboros/skills/ находит наш skill.

    `_bundled_skills_dir()` смотрит на `ouroboros.config.REPO_DIR` (где
    стоит Ouroboros desktop app), не на наш worktree. В CI / dev среде
    skill достаётся через user-repo path (env `OUROBOROS_SKILLS_REPO_PATH`
    или явный `repo_path` параметр).
    """
    from ouroboros.skill_loader import discover_skills

    drive_root = tmp_path / "drive"
    drive_root.mkdir()

    skills = discover_skills(
        drive_root,
        repo_path=str(REPO_ROOT / "skills"),
        include_bundled=False,
    )
    names = [s.name for s in skills]
    assert "neftegaz_analyst" in names, (
        f"neftegaz_analyst не найден в discover_skills; got: {names}"
    )

    skill = next(s for s in skills if s.name == "neftegaz_analyst")
    assert skill.manifest.is_extension()
    assert skill.manifest.entry == "plugin.py"
    assert skill.load_error == "", f"load_error: {skill.load_error}"


# =============================================================================
# register(api) через capture-mock
# =============================================================================


class _CaptureAPI:
    """Минимальный mock PluginAPI для проверки register-вызовов."""

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []
        self.routes: list[dict[str, Any]] = []
        self.ws_handlers: list[dict[str, Any]] = []
        self.ui_tabs: list[dict[str, Any]] = []
        self.logs: list[tuple[str, str]] = []

    def register_tool(self, name, handler, *, description, schema, timeout_sec=60):
        self.tools.append(
            {
                "name": name,
                "handler": handler,
                "description": description,
                "schema": schema,
                "timeout_sec": timeout_sec,
            }
        )

    def register_route(self, path, handler, *, methods=("GET",)):
        self.routes.append({"path": path, "handler": handler, "methods": tuple(methods)})

    def register_ws_handler(self, message_type, handler):
        self.ws_handlers.append({"type": message_type, "handler": handler})

    def register_ui_tab(self, tab_id, title, *, icon="extension", render=None):
        self.ui_tabs.append(
            {"tab_id": tab_id, "title": title, "icon": icon, "render": render or {}}
        )

    def log(self, level, message, **fields):
        self.logs.append((level, message))

    def get_settings(self, keys):
        return {}

    def get_state_dir(self):
        return "/tmp"


def _import_plugin_module():
    """Импорт `skills/neftegaz_analyst/plugin.py` без установки skill в data plane."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_neftegaz_analyst_plugin",
        SKILL_DIR / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_register_two_tools_and_route() -> None:
    """register(api) регистрирует analyst_query + rag_search + health route (см. ADR-0018)."""
    plugin = _import_plugin_module()
    api = _CaptureAPI()
    plugin.register(api)

    assert len(api.tools) == 2, (
        f"expected 2 tools (analyst_query + rag_search), got {[t['name'] for t in api.tools]}"
    )
    tool_names = {t["name"] for t in api.tools}
    assert tool_names == {"analyst_query", "rag_search"}, f"got: {tool_names}"

    aq = next(t for t in api.tools if t["name"] == "analyst_query")
    assert aq["timeout_sec"] == 120
    assert "Brent" in aq["description"]
    assert aq["schema"]["required"] == ["query"]

    rs = next(t for t in api.tools if t["name"] == "rag_search")
    assert rs["timeout_sec"] == 30
    # description должен иметь явный сигнал что это RAG / корпус
    assert ("RAG" in rs["description"]) or ("корпус" in rs["description"])
    assert rs["schema"]["type"] == "object"
    assert "query" in rs["schema"]["properties"]
    assert "k" in rs["schema"]["properties"]
    assert rs["schema"]["required"] == ["query"]
    assert rs["schema"]["properties"]["k"]["minimum"] == 1
    assert rs["schema"]["properties"]["k"]["maximum"] == 10

    assert len(api.routes) == 1
    assert api.routes[0]["path"] == "health"
    assert api.routes[0]["methods"] == ("GET",)

    assert len(api.ui_tabs) == 0
    assert len(api.ws_handlers) == 0


# =============================================================================
# _tool_analyst_query — input validation
# =============================================================================


def test_tool_empty_query_returns_error() -> None:
    plugin = _import_plugin_module()
    raw = plugin._tool_analyst_query(query="")
    payload = json.loads(raw)
    assert payload == {"error": "query is empty"}


def test_tool_too_long_query_returns_error() -> None:
    plugin = _import_plugin_module()
    raw = plugin._tool_analyst_query(query="а" * 2500)
    payload = json.loads(raw)
    assert "too long" in payload.get("error", "")


# =============================================================================
# _tool_analyst_query — happy path с mock'нутым графом
# =============================================================================


def test_tool_invokes_graph_and_returns_json(monkeypatch) -> None:
    """Mock build_analyst_graph + GraphState. Tool возвращает JSON со
    всеми ключами (synthesis, intent, citations, ...)."""

    plugin = _import_plugin_module()

    from nefteboros.forecast.schema import Horizon
    from nefteboros.graphs.state import Citation, Intent, IntentType

    fake_intent = Intent(
        type=IntentType.FORECAST_SIMPLE,
        forecast_assets=["brent"],
        forecast_horizon=Horizon.M3,
        matched_rule="rule_1_oil_default",
    )
    fake_citation = Citation(
        tag="[forecast_model:brent@3m, sarimax, ADR-0012]",
        kind="forecast_model",
        detail="asset=brent, method=sarimax, horizon=3m",
    )
    fake_final_state = {
        "query": "прогноз нефти на квартал",
        "intent": fake_intent,
        "forecast_results": [],
        "forecast_errors": [],
        "synthesis": "Brent на 3 месяца: $108.64, CI 80% [$87, $130]. ...",
        "citations": [fake_citation],
        "validation_warnings": [],
    }

    class _FakeCompiledGraph:
        async def ainvoke(self, state):
            return fake_final_state

    def fake_build_analyst_graph():
        return _FakeCompiledGraph()

    monkeypatch.setattr(
        "nefteboros.graphs.analyst_graph.build_analyst_graph",
        fake_build_analyst_graph,
    )

    raw = plugin._tool_analyst_query(query="прогноз нефти на квартал")
    payload = json.loads(raw)

    assert payload["synthesis"].startswith("Brent на 3 месяца")
    assert payload["intent"]["type"] == "forecast_simple"
    assert payload["intent"]["assets"] == ["brent"]
    assert payload["intent"]["horizon"] == "3m"
    assert payload["intent"]["matched_rule"] == "rule_1_oil_default"
    assert len(payload["citations"]) == 1
    assert payload["citations"][0]["kind"] == "forecast_model"
    assert payload["validation_warnings"] == []
    assert payload["forecast_errors"] == []


def test_tool_handles_graph_runtime_error(monkeypatch) -> None:
    """Если ainvoke бросит exception — handler не падает, возвращает error JSON."""
    plugin = _import_plugin_module()

    class _AngryGraph:
        async def ainvoke(self, state):
            raise RuntimeError("forecast() data unavailable")

    monkeypatch.setattr(
        "nefteboros.graphs.analyst_graph.build_analyst_graph",
        lambda: _AngryGraph(),
    )

    raw = plugin._tool_analyst_query(query="прогноз нефти на квартал")
    payload = json.loads(raw)
    assert "error" in payload
    assert "RuntimeError" in payload["error"]


# =============================================================================
# _tool_rag_search — input validation
# =============================================================================


def test_rag_search_empty_query_returns_error() -> None:
    plugin = _import_plugin_module()
    raw = plugin._tool_rag_search(query="")
    payload = json.loads(raw)
    assert payload == {"error": "query is empty"}


def test_rag_search_too_long_query_returns_error() -> None:
    plugin = _import_plugin_module()
    raw = plugin._tool_rag_search(query="а" * 2500)
    payload = json.loads(raw)
    assert "too long" in payload.get("error", "")


def test_rag_search_clamps_k_to_max(monkeypatch) -> None:
    """k > _RAG_MAX_K (10) clamp'ится; tool не падает."""
    plugin = _import_plugin_module()

    captured_k = {}

    class _FakeHit:
        def __init__(self, idx):
            self.chunk_id = f"src__{idx:04d}"
            self.text = f"chunk {idx}"
            self.bi_encoder_score = 0.8
            self.rerank_score = 0.8
            self.metadata = {
                "source_id": "src",
                "source_title": "Test source",
                "section_path": "ch1 > sec1",
                "page_start": 1,
                "page_end": 1,
                "language": "ru",
                "block": "1_strategy",
                "type": "annual_report",
            }

    class _FakeRetriever:
        def retrieve(self, query, *, k_dense, k_final, **kw):
            captured_k["k_final"] = k_final
            return [_FakeHit(i) for i in range(k_final)]

    import sys
    fake_module = type(sys)("nefteboros.rag.retriever")
    fake_module.Retriever = _FakeRetriever
    monkeypatch.setitem(sys.modules, "nefteboros.rag.retriever", fake_module)

    raw = plugin._tool_rag_search(query="любой запрос", k=999)
    payload = json.loads(raw)
    assert "error" not in payload
    assert payload["k"] == 10
    assert captured_k["k_final"] == 10
    assert payload["total_returned"] == 10


def test_rag_search_happy_path(monkeypatch) -> None:
    """rag_search с mock'нутым Retriever возвращает корректный JSON shape."""
    plugin = _import_plugin_module()

    class _FakeHit:
        def __init__(self):
            self.chunk_id = "opec_woo_2025__0042"
            self.text = "OPEC прогнозирует спрос на нефть на уровне 110 mb/d к 2050..."
            self.bi_encoder_score = 0.785
            self.rerank_score = 0.785
            self.metadata = {
                "source_id": "opec_woo_2025",
                "source_title": "OPEC World Oil Outlook 2025 (full)",
                "section_path": "Long-term outlook > Demand projection",
                "page_start": 142,
                "page_end": 144,
                "language": "en",
                "block": "1_strategy",
                "type": "institutional_forecast",
            }

    class _FakeRetriever:
        def retrieve(self, query, *, k_dense, k_final, **kw):
            return [_FakeHit() for _ in range(min(k_final, 1))]

    import sys
    fake_module = type(sys)("nefteboros.rag.retriever")
    fake_module.Retriever = _FakeRetriever
    monkeypatch.setitem(sys.modules, "nefteboros.rag.retriever", fake_module)

    raw = plugin._tool_rag_search(query="прогноз спроса на нефть к 2050", k=3)
    payload = json.loads(raw)

    assert "error" not in payload
    assert payload["query"] == "прогноз спроса на нефть к 2050"
    assert payload["k"] == 3
    assert payload["total_returned"] == 1
    assert len(payload["chunks"]) == 1

    chunk = payload["chunks"][0]
    assert chunk["chunk_id"] == "opec_woo_2025__0042"
    assert chunk["score"] == 0.785
    assert chunk["source_id"] == "opec_woo_2025"
    assert chunk["source_title"] == "OPEC World Oil Outlook 2025 (full)"
    assert chunk["page_start"] == 142
    assert chunk["page_end"] == 144
    assert chunk["language"] == "en"
    assert chunk["block"] == "1_strategy"
    assert chunk["type"] == "institutional_forecast"
    assert chunk["text"].startswith("OPEC прогнозирует")


def test_rag_search_handles_retriever_error(monkeypatch) -> None:
    """Если Retriever упал — handler не падает, возвращает error JSON."""
    plugin = _import_plugin_module()

    class _AngryRetriever:
        def retrieve(self, *args, **kwargs):
            raise RuntimeError("Chroma collection not found")

    import sys
    fake_module = type(sys)("nefteboros.rag.retriever")
    fake_module.Retriever = lambda: _AngryRetriever()
    monkeypatch.setitem(sys.modules, "nefteboros.rag.retriever", fake_module)

    raw = plugin._tool_rag_search(query="любой", k=3)
    payload = json.loads(raw)
    assert "error" in payload
    assert "RuntimeError" in payload["error"]
