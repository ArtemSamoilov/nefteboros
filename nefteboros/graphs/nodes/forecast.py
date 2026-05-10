"""forecast_call — узел графа, дёргает forecast() для активов из intent.

Lazy import nefteboros.forecast.api.forecast, чтобы граф можно было
импортировать без heavy domain stack (pandas/numpy/statsmodels/yfinance/
sklearn) — например, в Ouroboros CI.

Sequential по (asset, scenario) парам. Multi-scenario запросы
(intent.forecast_scenarios=['bear','base','bull']) → N×M вызовов:
каждый asset прогнозируется во всех запрошенных сценариях, результаты
собираются в плоский список (synthesize группирует по asset/scenario).
Параллелизация (asyncio.gather) — отложена в integration PR (когда будут
параллельные RAG/web узлы и общий perf-проход имеет смысл).
"""

from __future__ import annotations

import logging
from typing import Any, Union

from nefteboros.graphs.state import GraphState

logger = logging.getLogger(__name__)


# Default horizon, если intent.forecast_horizon не извлечён из запроса.
# 3m — баланс: короче endogenous CI shrinks; длиннее теряют точечную
# полезность. См. ADR-0012 §«Горизонты прогноза».
_DEFAULT_HORIZON = "3m"


async def forecast_call(state: GraphState) -> dict[str, Any]:
    """Узел: forecast() для каждого актива из intent.forecast_assets.

    Не падает на ошибках одного актива — собирает их в forecast_errors,
    остальные активы прогнозируются. Узел graph должен быть resilient.

    Возвращает partial-update: forecast_results + forecast_errors.
    LangGraph мерджит это в GraphState.
    """
    if state.intent is None:
        return {
            "forecast_errors": [
                "forecast_call вызван без intent — ошибка graph wiring."
            ],
        }

    if not state.intent.forecast_assets:
        return {
            "forecast_errors": [
                f"forecast_call: intent.type={state.intent.type.value} без assets — "
                "graph misroute (ожидался forecast_simple/forecast_with_context).",
            ],
        }

    horizon = state.intent.forecast_horizon
    horizon_str = horizon.value if horizon is not None else _DEFAULT_HORIZON

    # ['base'] если scenarios не указаны (single-scenario default).
    # Multi-scenario запросы дают ['bear','base','bull'] от classify_intent.
    scenarios = state.intent.forecast_scenarios or ["base"]

    # Lazy import — heavy stack
    from nefteboros.forecast.api import forecast as forecast_fn
    from nefteboros.forecast.schema import ForecastRefusal, ForecastResult

    results: list[Union[ForecastResult, ForecastRefusal]] = []
    errors: list[str] = []

    for asset in state.intent.forecast_assets:
        for scenario in scenarios:
            try:
                result = forecast_fn(
                    asset=asset,
                    horizon=horizon_str,
                    scenario=scenario,
                    method=state.intent.forecast_method,
                )
                results.append(result)
            except (ValueError, KeyError, RuntimeError) as exc:
                err_msg = (
                    f"forecast({asset!r}, {horizon_str!r}, scenario={scenario!r}) → "
                    f"{type(exc).__name__}: {exc}"
                )
                logger.warning(err_msg)
                errors.append(err_msg)
            except Exception as exc:  # noqa: BLE001 — graph node must not crash
                err_msg = (
                    f"forecast({asset!r}, {horizon_str!r}, scenario={scenario!r}) → "
                    f"unexpected {type(exc).__name__}: {exc}"
                )
                logger.exception(
                    "forecast_call: unexpected error for %s/%s", asset, scenario
                )
                errors.append(err_msg)

    return {
        "forecast_results": results,
        "forecast_errors": errors,
    }


__all__ = ["forecast_call"]
