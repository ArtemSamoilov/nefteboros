"""Task-start tool visibility policy.

This module determines which tools are available at the start of a task
without an explicit ``enable_tools`` call.

Tool sets are imported from ``ouroboros.tool_capabilities`` (the single
source of truth).  This module adds the visibility-decision logic on top.

**Auto-enable extension skills** (env-driven, см. ADR-0017):

``OUROBOROS_AUTO_ENABLE_SKILLS=skill_name:short_tool_name[,…]`` — список
extension-skill tool'ов, которые попадают в initial schemas вместе с
core/meta tools. Без этого — extension tool появится только после
``enable_tools(...)`` discovery round'а.

Формат:
- ``"neftegaz_analyst:analyst_query"`` — skill name + short tool name.
  Резолв через ``extension_loader.extension_surface_name`` →
  full extension tool name (``ext_<len>_<token>_<short>``).
- Невалидные / нерезолвящиеся записи игнорируются (env остаётся валидным
  даже при rename / disable одного из skill'ов).

Это даёт agent'у domain skill сразу в первом round'е — LLM видит его
description в tool spec, выбирает правильно на доменных запросах.
Для skills вне white-list'а — стандартный discovery
(``list_available_tools`` / ``enable_tools``).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Protocol

from ouroboros.tool_capabilities import CORE_TOOL_NAMES, META_TOOL_NAMES


_AUTO_ENABLE_ENV = "OUROBOROS_AUTO_ENABLE_SKILLS"


class ToolSchemaProvider(Protocol):
    """Minimal registry contract needed by the loop/discovery helpers."""

    def schemas(self, core_only: bool = False) -> List[Dict[str, Any]]:
        ...


def _parse_auto_enable_env() -> set[str]:
    """Parse ``OUROBOROS_AUTO_ENABLE_SKILLS`` into a set of full extension tool names.

    Returns empty set on missing/empty env or when ``extension_loader`` cannot
    be imported. Никогда не raise — fail-soft, чтобы пустой/битый env не
    ломал loop start.
    """
    raw = os.environ.get(_AUTO_ENABLE_ENV, "").strip()
    if not raw:
        return set()

    try:
        from ouroboros.extension_loader import extension_surface_name
    except ImportError:
        return set()

    out: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            skill, short = entry.split(":", 1)
            skill = skill.strip()
            short = short.strip()
        else:
            # Без ":" — short = skill (legacy / симметричный case).
            skill = entry
            short = entry
        if not skill or not short:
            continue
        try:
            out.add(extension_surface_name(skill, short))
        except Exception:
            # Невалидный skill/short → пропускаем, остальные записи всё равно работают.
            continue
    return out


def is_initial_task_tool(name: str) -> bool:
    """Return True if the tool should be loaded before any enable_tools call.

    Включает:
    - встроенные core tools (``CORE_TOOL_NAMES``);
    - meta-tools (``META_TOOL_NAMES`` — ``list_available_tools``, ``enable_tools``);
    - extension-skill tools, явно white-list'ленные через env
      ``OUROBOROS_AUTO_ENABLE_SKILLS`` (см. модуль-docstring).
    """
    if name in CORE_TOOL_NAMES or name in META_TOOL_NAMES:
        return True
    return name in _parse_auto_enable_env()


def initial_tool_schemas(registry: ToolSchemaProvider) -> List[Dict[str, Any]]:
    """Return the schemas that should be present from round 1."""

    result = []
    for schema in registry.schemas():
        name = schema.get("function", {}).get("name", "")
        if is_initial_task_tool(name):
            result.append(schema)
    return result


def list_non_core_tools(registry: ToolSchemaProvider) -> List[Dict[str, str]]:
    """Return name+description for tools that require explicit enable_tools."""

    result = []
    for schema in registry.schemas():
        function = schema.get("function", {})
        name = function.get("name", "")
        if not name or is_initial_task_tool(name):
            continue
        result.append({
            "name": name,
            "description": function.get("description", "No description"),
        })
    return result
