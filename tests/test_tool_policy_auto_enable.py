"""Тесты для env-driven auto-enable extension skill tools.

См. ADR-0017. Проверяет, что `OUROBOROS_AUTO_ENABLE_SKILLS=neftegaz_analyst:analyst_query`
заставляет `initial_tool_schemas` включать наш extension tool на первом
round'е task'а — агент видит description без discovery-round'ов.
"""

from __future__ import annotations

from typing import Any, Dict, List


# Полные ext-имена резолвятся через extension_loader.extension_surface_name:
#   skill_name="neftegaz_analyst" → token="r_neftegaz_analyst" (18 chars)
#   short="analyst_query"
#   full = "ext_18_r_neftegaz_analyst_analyst_query"
#
# Tests инкапсулируют это значение, чтобы при rename ловить через failure.
NEFTEGAZ_FULL = "ext_18_r_neftegaz_analyst_analyst_query"
WEATHER_FULL = "ext_9_r_weather_fetch"


class _FakeRegistry:
    """Минимальный ToolSchemaProvider для unit-теста — отдаёт фиксированный список."""

    def __init__(self, schemas: List[Dict[str, Any]]):
        self._schemas = schemas

    def schemas(self, core_only: bool = False):
        return self._schemas


def _fn_schema(name: str, description: str = "stub") -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_extension_surface_name_resolves_as_expected() -> None:
    """Sanity: extension_surface_name даёт ожидаемое full-имя для нашего skill'а."""
    from ouroboros.extension_loader import extension_surface_name

    assert (
        extension_surface_name("neftegaz_analyst", "analyst_query")
        == NEFTEGAZ_FULL
    )


def test_initial_includes_only_core_when_env_unset(monkeypatch) -> None:
    """Без OUROBOROS_AUTO_ENABLE_SKILLS — extension tool отсутствует в initial."""
    monkeypatch.delenv("OUROBOROS_AUTO_ENABLE_SKILLS", raising=False)

    from ouroboros.tool_policy import initial_tool_schemas

    reg = _FakeRegistry(
        [
            _fn_schema("repo_read"),  # CORE
            _fn_schema(NEFTEGAZ_FULL),  # extension
            _fn_schema(WEATHER_FULL),  # extension
        ]
    )
    schemas = initial_tool_schemas(reg)
    names = {s["function"]["name"] for s in schemas}

    assert "repo_read" in names
    assert NEFTEGAZ_FULL not in names
    assert WEATHER_FULL not in names


def test_initial_includes_whitelisted_extension(monkeypatch) -> None:
    """С env-флагом наш ext-tool попадает в initial schemas."""
    monkeypatch.setenv(
        "OUROBOROS_AUTO_ENABLE_SKILLS",
        "neftegaz_analyst:analyst_query",
    )

    from ouroboros.tool_policy import initial_tool_schemas

    reg = _FakeRegistry(
        [
            _fn_schema("repo_read"),
            _fn_schema(NEFTEGAZ_FULL),
            _fn_schema(WEATHER_FULL),  # не whitelisted
        ]
    )
    schemas = initial_tool_schemas(reg)
    names = {s["function"]["name"] for s in schemas}

    assert "repo_read" in names
    assert NEFTEGAZ_FULL in names
    assert WEATHER_FULL not in names  # вне white-list'а


def test_multiple_skills_supported(monkeypatch) -> None:
    """Запятая-отделённый список skills — все попадают в initial."""
    monkeypatch.setenv(
        "OUROBOROS_AUTO_ENABLE_SKILLS",
        "neftegaz_analyst:analyst_query,weather:fetch",
    )

    from ouroboros.tool_policy import initial_tool_schemas

    reg = _FakeRegistry([_fn_schema(NEFTEGAZ_FULL), _fn_schema(WEATHER_FULL)])
    schemas = initial_tool_schemas(reg)
    names = {s["function"]["name"] for s in schemas}

    assert NEFTEGAZ_FULL in names
    assert WEATHER_FULL in names


def test_invalid_entries_are_skipped_silently(monkeypatch) -> None:
    """Пустые/битые записи игнорируются, валидные работают."""
    monkeypatch.setenv(
        "OUROBOROS_AUTO_ENABLE_SKILLS",
        ",,neftegaz_analyst:analyst_query,,broken:,:nameonly",
    )

    from ouroboros.tool_policy import initial_tool_schemas

    reg = _FakeRegistry([_fn_schema(NEFTEGAZ_FULL)])
    schemas = initial_tool_schemas(reg)
    names = {s["function"]["name"] for s in schemas}

    assert NEFTEGAZ_FULL in names


def test_is_initial_task_tool_direct_calls(monkeypatch) -> None:
    """Прямая проверка is_initial_task_tool: core/whitelisted/random."""
    monkeypatch.setenv(
        "OUROBOROS_AUTO_ENABLE_SKILLS",
        "neftegaz_analyst:analyst_query",
    )

    from ouroboros.tool_policy import is_initial_task_tool

    assert is_initial_task_tool("repo_read")
    assert is_initial_task_tool(NEFTEGAZ_FULL)
    assert not is_initial_task_tool("ext_some_other_tool")
    assert not is_initial_task_tool("not_a_tool")


def test_list_non_core_excludes_whitelisted(monkeypatch) -> None:
    """list_non_core_tools не возвращает уже-в-initial extension tools.

    Это критично для UX: hint message в loop.py не должен советовать enable_tools
    для skill'а, который и так уже active.
    """
    monkeypatch.setenv(
        "OUROBOROS_AUTO_ENABLE_SKILLS",
        "neftegaz_analyst:analyst_query",
    )

    from ouroboros.tool_policy import list_non_core_tools

    reg = _FakeRegistry([_fn_schema(NEFTEGAZ_FULL), _fn_schema(WEATHER_FULL)])
    non_core = list_non_core_tools(reg)
    names = {t["name"] for t in non_core}

    assert NEFTEGAZ_FULL not in names  # auto-enabled, не в non-core
    assert WEATHER_FULL in names  # не whitelisted, остаётся в non-core
