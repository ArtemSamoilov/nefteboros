"""Skill neftegaz_analyst — точка входа PluginAPI v1.

PLACEHOLDER. Реальная реализация — в PR `feature/skill-integration`.
Пока регистрирует только healthcheck-route, чтобы skill был валиден для loader'а.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse


async def _route_health(request: Request) -> JSONResponse:
    """Healthcheck — placeholder."""
    return JSONResponse(
        {
            "status": "skeleton",
            "message": "neftegaz_analyst skill loaded (placeholder). "
            "Real implementation pending in feature/skill-integration.",
        }
    )


def register(api: Any) -> None:
    """PluginAPI v1 entry point."""
    api.register_route("health", _route_health, methods=("GET",))
    api.log("info", "neftegaz_analyst: skeleton loaded — see SKILL.md")


__all__ = ["register"]
