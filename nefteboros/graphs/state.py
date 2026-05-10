"""GraphState и intent-схемы для analyst LangGraph subgraph.

См. ADR-0014 — graph-first архитектура с rule-based classify_intent.

GraphState мутируется по мере прохождения узлов (classify → forecast →
synthesize → validate). Все поля Optional / list — узлы могут падать
по своим причинам, мы не теряем то, что успели собрать.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from nefteboros.forecast.schema import (
    ForecastRefusal,
    ForecastResult,
    Horizon,
    ModelMethod,
)


# =============================================================================
# Intent — выход classify_intent, вход routing-edges
# =============================================================================


class IntentType(str, Enum):
    """Типы intent'ов согласно 5 правилам ADR-0013 §«Constraints for SKILL.md».

    - **forecast_simple** — generic forecast (правило #1 default ветки):
      «нефть»→brent; «газ»→henry_hub+ttf; «WTI»→wti; «TTF»→ttf и т.п.
    - **forecast_with_context** — forecast в РФ-контексте (правило #1 РФ ветка):
      «бюджет/Минфин/НДПИ/российская нефть» → brent + urals + urals_minfin_blend.
    - **russian_gas_refusal** — запрос про прямые цены РФ-газа (правило #5):
      «цена газа в России в рублях» → честный отказ + redirect к TTF/GAZP/RAG.
    - **out_of_scope** — горизонт <1m / >=18m (правило #3) или вне доменных правил.

    Правила #2 (unknown asset → web-search → semantic family → proxy) и
    #4 (derived без method → fail loudly) реализуются в integration PR'ах
    (`feature/web-search` для #2; правка `forecast.api` для #4 — отдельный PR).
    Здесь они НЕ enforced.
    """

    FORECAST_SIMPLE = "forecast_simple"
    FORECAST_WITH_CONTEXT = "forecast_with_context"
    RUSSIAN_GAS_REFUSAL = "russian_gas_refusal"
    OUT_OF_SCOPE = "out_of_scope"


class Intent(BaseModel):
    """Результат classify_intent. Используется conditional edges для routing'а.

    `matched_rule` — для debug / тестов; сообщает какое именно правило
    отработало в classify (полезно при триаже false-positive/false-negative).

    `forecast_scenarios` — список scenario-имён для forecast_call. Default
    ``["base"]`` (single base scenario). Multi-scenario запросы
    («сценарии bear/base/bull», «стресс-тест») триггерят
    ``["bear", "base", "bull"]`` — forecast_call делает N×M вызовов.
    """

    model_config = ConfigDict(frozen=True)

    type: IntentType
    forecast_assets: list[str] = Field(default_factory=list)
    forecast_scenarios: list[str] = Field(default_factory=lambda: ["base"])
    forecast_horizon: Optional[Horizon] = None
    forecast_method: Optional[ModelMethod] = None
    refuse_reason: Optional[str] = None
    matched_rule: Optional[str] = None


# =============================================================================
# Citation — ссылка на источник в synthesis
# =============================================================================


class Citation(BaseModel):
    """Один источник, на который ссылается synthesis.

    В minimal-graph (этот PR) — только `forecast_model` и `forecast_metadata`
    (RAG/web ещё нет). В integration PR'ах добавятся `rag_chunk` и `web_url`
    через расширение validate_citations.
    """

    model_config = ConfigDict(frozen=True)

    tag: str
    kind: Literal["forecast_model", "forecast_metadata", "rag_chunk", "web_url"]
    detail: str = ""


# =============================================================================
# GraphState — полное состояние LangGraph subgraph
# =============================================================================


class GraphState(BaseModel):
    """Полное состояние analyst graph'а.

    LangGraph мутирует это состояние по мере прохождения узлов. Узлы
    возвращают partial-dict, который мерджится в state. Все поля имеют
    sensible defaults — узел может пропуститься без поломки графа.
    """

    query: str
    intent: Optional[Intent] = None
    forecast_results: list[Union[ForecastResult, ForecastRefusal]] = Field(
        default_factory=list
    )
    forecast_errors: list[str] = Field(default_factory=list)
    synthesis: str = ""
    citations: list[Citation] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)


__all__ = [
    "Citation",
    "GraphState",
    "Intent",
    "IntentType",
]
