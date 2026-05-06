"""Pydantic-контракты для расчётного модуля.

Все публичные типы импортируются из `nefteboros.forecast` (через __init__).
Внутренние типы используются только в подмодулях.

Соглашения:
  - Цены — float, единицы измерения зависят от актива (см. registry.AssetMeta.unit).
  - Даты — pandas.Timestamp (как datetime), в моделях храним ISO-строкой для
    сериализации; в runtime используем pd.Timestamp.
  - Все NaN/inf проверяются на входе; в результате — только finite numbers.

См. ADR-0012 для архитектуры и обоснований.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# Перечисления
# =============================================================================

# Asset ID — строки, не Enum, чтобы было легко расширять без правки кода.
# Канонический список — registry.ASSET_REGISTRY.
AssetID = Literal[
    "brent", "wti",
    "urals", "espo", "urals_minfin_blend",
    "henry_hub", "ttf",
    "moexog", "gazp", "nvtk",
    # P1 / опционально:
    "opec_basket",
]
# JKM (Asian LNG) — отложен в P2: investing.com отдаёт только последний месяц
# через __NEXT_DATA__, AJAX-endpoint требует реверса; в interpret.py для
# Asian gas вопросов TTF используется как ближайший proxy. См. ADR-0012.


class Horizon(str, Enum):
    """Поддерживаемые горизонты прогноза.

    Меньше 1m — не делаем (дей-трейдинг, не наша область).
    Больше 12m — возвращаем отказ + перенаправление на сценарии в RAG.
    """

    M1 = "1m"
    M3 = "3m"
    M6 = "6m"
    M12 = "12m"

    @property
    def months(self) -> int:
        return {"1m": 1, "3m": 3, "6m": 6, "12m": 12}[self.value]

    @property
    def trading_days(self) -> int:
        # Грубая оценка: 21 trading day на месяц.
        return self.months * 21


class Frequency(str, Enum):
    DAILY = "daily"
    MONTHLY = "monthly"


class AssetGroup(str, Enum):
    OIL_GLOBAL = "oil_global"
    OIL_RUSSIAN = "oil_russian"
    GAS_GLOBAL = "gas_global"
    # Прямых daily-цен российского газа в открытых источниках нет (SPIMEX за платным
    # каналом, CBR-monthly мёртв post-2022). Вместо них — рыночный proxy сектора:
    # MOEX Oil & Gas Index + Газпром/Новатэк акции через MOEX ISS.
    RUSSIAN_ENERGY_PROXY = "russian_energy_proxy"


class DataSource(str, Enum):
    YFINANCE = "yfinance"
    EIA = "eia"
    INVESTING = "investing.com"
    MOEX_ISS = "moex_iss"
    OPEC = "opec"
    DERIVED = "derived"


class ModelMethod(str, Enum):
    """Канонические идентификаторы моделей.

    Не путать с конкретными классами в `models/` — это публичные имена,
    которые могут возвращаться в `ForecastResult.method` и использоваться
    в API per-call override.
    """

    RANDOM_WALK = "random_walk"
    SARIMAX = "sarimax"
    XGBOOST = "xgboost"
    ENSEMBLE = "ensemble"


class BacktestRegime(str, Enum):
    """Сегменты для regime-aware бектеста (см. ADR-0012)."""

    PRE_2022 = "pre_2022"                    # 2021-01 — 2022-02
    RUSSIA_WAR_SHOCK = "russia_war_shock"    # 2022-02 — 2022-12
    CAP_NORMALIZATION = "cap_normalization"  # 2023-01 — 2025-12
    IRAN_2026 = "iran_2026"                  # 2026-01 — наст.
    AGGREGATE = "aggregate"                  # без сегментации


# =============================================================================
# Доверительный интервал
# =============================================================================


class ConfidenceInterval(BaseModel):
    """Симметричный/асимметричный CI для одного прогнозного значения.

    `level` — номинальный уровень покрытия (0.80, 0.95). Эмпирическое покрытие
    для конкретной модели — в BacktestMetrics.coverage_<level>.
    """

    model_config = ConfigDict(frozen=True)

    level: float = Field(..., ge=0.0, le=1.0)
    low: float
    high: float

    @field_validator("high")
    @classmethod
    def _high_ge_low(cls, v: float, info: Any) -> float:
        low = info.data.get("low")
        if low is not None and v < low:
            raise ValueError(f"CI high ({v}) must be >= low ({low})")
        return v

    @property
    def width(self) -> float:
        return self.high - self.low


# =============================================================================
# Прогнозные точки и результат
# =============================================================================


class ForecastPoint(BaseModel):
    """Одна точка прогноза (для конкретной даты в горизонте).

    Большинство моделей возвращают точечную оценку на конец горизонта;
    промежуточные точки — опциональны и зависят от модели.
    """

    model_config = ConfigDict(frozen=True)

    date: datetime
    value: float
    ci_80: ConfidenceInterval
    ci_95: ConfidenceInterval


# =============================================================================
# Метрики бектеста
# =============================================================================


class BacktestMetrics(BaseModel):
    """Метрики качества модели на одном сегменте бектеста.

    `n_forecasts` — число rolling-точек, на которых считались метрики.
    `mase_vs_rw` < 1 = модель обыгрывает random walk; >= 1 = не обыгрывает.
    """

    model_config = ConfigDict(frozen=True)

    regime: BacktestRegime
    n_forecasts: int = Field(..., ge=0)

    mape: Optional[float] = None  # %
    rmse: Optional[float] = None  # в единицах цены
    coverage_80: Optional[float] = None  # доля попаданий факта в CI 80%
    coverage_95: Optional[float] = None
    mase_vs_rw: Optional[float] = None
    directional_accuracy: Optional[float] = None  # доля верно угаданных знаков

    notes: Optional[str] = None  # для known-issues per-regime (см. ADR-0012)


class BacktestSummary(BaseModel):
    """Полный набор бектест-метрик для одной (asset, model, horizon) тройки."""

    asset: str
    horizon: Horizon
    method: ModelMethod
    train_window_years: float
    history_window_years: float
    rolling_step_months: int
    per_regime: list[BacktestMetrics]


# =============================================================================
# Главный результат
# =============================================================================


class ForecastResult(BaseModel):
    """Результат вызова `forecast(asset, horizon)`.

    `points` — список прогнозных точек (как минимум одна — на конец горизонта).
    `interpretation` — горизонт-aware текст для агента (см. interpret.py).
    `backtest_summary` — out-of-sample метрики выбранной модели; используется
    интерпретатором, чтобы честно сообщать пользователю качество.
    `metadata` — служебная информация (источник данных, дата последней цены,
    был ли использован кеш, версии библиотек).
    """

    asset: str
    horizon: Horizon
    method: ModelMethod
    points: list[ForecastPoint]
    interpretation: str
    backtest_summary: Optional[BacktestSummary] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def end_point(self) -> ForecastPoint:
        return self.points[-1]


# =============================================================================
# Refusal — для horizon >= 18m
# =============================================================================


class ForecastRefusal(BaseModel):
    """Возвращается, когда horizon вне области честной точечной оценки.

    Не исключение — это нормальный ответ, который агент должен использовать
    для перенаправления на сценарные прогнозы в RAG-корпусе.
    """

    asset: str
    requested_horizon_months: int
    reason: str
    redirect_to: list[str] = Field(
        default_factory=lambda: [
            "OPEC World Oil Outlook 2025",
            "IEA Oil 2025 — Analysis and Forecast to 2030",
            "IEA Gas 2025 — Analysis and Forecasts to 2030",
            "ИНЭИ РАН — Прогноз развития энергетики мира и России 2024",
        ]
    )


__all__ = [
    "AssetID",
    "Horizon",
    "Frequency",
    "AssetGroup",
    "DataSource",
    "ModelMethod",
    "BacktestRegime",
    "ConfidenceInterval",
    "ForecastPoint",
    "BacktestMetrics",
    "BacktestSummary",
    "ForecastResult",
    "ForecastRefusal",
]
