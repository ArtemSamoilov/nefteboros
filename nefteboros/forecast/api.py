"""High-level forecast API: единая точка входа для агента и CLI.

    forecast(asset, horizon, method=None) -> ForecastResult | ForecastRefusal

Логика:
  1. Валидация horizon: parse string → Horizon | Refusal | ValueError.
  2. Lookup asset в registry.
  3. Если asset = derived (urals/espo/blend) → рекурсивно прогнозируем base
     (Brent), применяем derived layer.
  4. Иначе:
     - fetch history через подходящий fetcher (yfinance/eia/moex).
     - применяем log-transform если meta.log_transform.
     - выбираем method (явный override или per-horizon default).
     - fit → predict.
     - откатываем log-transform.
     - строим interpretation.
     - возвращаем ForecastResult.

См. ADR-0012 §«Горизонты прогноза» / Конфигурация.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Optional, Union

import numpy as np
import pandas as pd

from nefteboros.forecast.data.eia import fetch_eia_for_asset
from nefteboros.forecast.data.moex import fetch_moex
from nefteboros.forecast.data.yf import fetch_yfinance
from nefteboros.forecast.derived_layer import (
    derive_espo_forecast,
    derive_minfin_blend_forecast,
    derive_urals_forecast,
)
from nefteboros.forecast.interpret import generate_interpretation
from nefteboros.forecast.models.base import BaseForecaster
from nefteboros.forecast.models.ensemble import EnsembleForecaster
from nefteboros.forecast.models.random_walk import RandomWalkForecaster
from nefteboros.forecast.models.sarimax import SARIMAXForecaster
from nefteboros.forecast.models.xgboost_m import XGBoostForecaster
from nefteboros.forecast.registry import get_asset
from nefteboros.forecast.schema import (
    ConfidenceInterval,
    DataSource,
    ForecastPoint,
    ForecastRefusal,
    ForecastResult,
    Horizon,
    ModelMethod,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Default method выбор per (asset, horizon)
# Будет уточнён по результатам бектеста в docs/experiments/forecast.md.
# Пока — sensible defaults на основе literature (Baumeister-Kilian, MDPI 2025).
# =============================================================================

_DEFAULT_METHOD_BY_HORIZON = {
    Horizon.M1: ModelMethod.RANDOM_WALK,    # на 1m RW почти всегда лучший
    Horizon.M3: ModelMethod.ENSEMBLE,
    Horizon.M6: ModelMethod.ENSEMBLE,
    Horizon.M12: ModelMethod.ENSEMBLE,
}


# =============================================================================
# Public API
# =============================================================================


def forecast(
    asset: str,
    horizon: Union[str, Horizon],
    *,
    method: Optional[Union[str, ModelMethod]] = None,
    history_years: float = 5.0,
    use_cache: bool = True,
) -> Union[ForecastResult, ForecastRefusal]:
    """Прогноз цены актива на горизонт.

    Args:
        asset: один из ASSET_REGISTRY (brent, wti, urals, ...).
        horizon: "1m" / "3m" / "6m" / "12m". Поддержка "1d"/"1w" — отсутствует
                 (область дей-трейдинга). >= 18m → ForecastRefusal.
        method: переопределение модели. Если None — default per horizon.
        history_years: сколько лет истории брать для обучения.
        use_cache: использовать ли локальный кеш данных.

    Returns:
        ForecastResult с centroidом, CI 80/95, interpretation, metadata.
        ForecastRefusal если horizon вне области точечных прогнозов.

    Raises:
        ValueError: невалидный asset или horizon-формат.
        RuntimeError: данные недоступны и кеш пуст.
    """
    # 1. Validate horizon
    horizon_parsed = _parse_horizon(asset, horizon)
    if isinstance(horizon_parsed, ForecastRefusal):
        return horizon_parsed
    h: Horizon = horizon_parsed

    # 2. Validate asset
    meta = get_asset(asset)

    # 3. Validate method
    if method is None:
        chosen_method = _default_method_for(asset, h)
    else:
        chosen_method = method if isinstance(method, ModelMethod) else ModelMethod(method)
    if chosen_method not in meta.available_methods:
        raise ValueError(
            f"asset={asset!r} не поддерживает method={chosen_method.value!r}. "
            f"Доступны: {[m.value for m in meta.available_methods]}"
        )

    logger.info(
        "forecast: asset=%s horizon=%s method=%s",
        asset, h.value, chosen_method.value,
    )

    # 4. Routing: derived vs observable
    if meta.primary_source == DataSource.DERIVED:
        return _forecast_derived(asset, h, chosen_method, history_years, use_cache)

    return _forecast_observable(asset, h, chosen_method, history_years, use_cache)


# =============================================================================
# Internal — observable path
# =============================================================================


def _forecast_observable(
    asset: str,
    horizon: Horizon,
    method: ModelMethod,
    history_years: float,
    use_cache: bool,
) -> ForecastResult:
    meta = get_asset(asset)

    since = pd.Timestamp.now(tz="UTC").normalize() - pd.DateOffset(
        years=int(math.ceil(history_years))
    )
    history = _fetch_history(asset, since=since, use_cache=use_cache)

    if history.empty or len(history) < 30:
        raise RuntimeError(
            f"forecast: недостаточно данных для {asset!r} "
            f"(got n={len(history)}, need >=30)"
        )

    # Apply log-transform (для газовых рядов с экстремумами)
    # `(history > 0).all()` returns ``numpy.bool_`` (pandas Series.all()), not
    # Python ``bool``. Python ``and`` returns the second operand as-is, so
    # ``use_log`` stays ``numpy.bool_``. Pydantic v2 + numpy 2.x cannot
    # serialize ``numpy.bool_`` in ``model_dump(mode="json")``, which crashes
    # the synthesize node downstream. ``bool(...)`` coerces to native Python.
    use_log = bool(meta.log_transform and (history > 0).all())
    if use_log:
        history_for_model = np.log(history)
        history_for_model.name = history.name
    else:
        history_for_model = history

    # Build & fit model
    model = _build_model(method)
    model.fit(history_for_model)

    # Predict
    raw_points = model.predict(horizon.months, levels=(0.80, 0.95))

    # Inverse log-transform if used
    if use_log:
        raw_points = [_exp_point(p) for p in raw_points]

    # Build result
    result = ForecastResult(
        asset=asset,
        horizon=horizon,
        method=method,
        points=raw_points,
        interpretation="",  # заполним ниже
        backtest_summary=None,  # заполняется бектестом отдельно (eval-скрипт)
        metadata={
            "primary_source": meta.primary_source.value,
            "data_n_points": len(history),
            "data_first_observation": str(history.index.min().date()),
            "data_last_observation": str(history.index.max().date()),
            "data_last_value": float(history.iloc[-1]),
            "log_transform_applied": use_log,
            "history_years_requested": history_years,
        },
    )
    # Interpretation
    result_with_text = result.model_copy(update={
        "interpretation": generate_interpretation(result),
    })
    return result_with_text


def _exp_point(p: ForecastPoint) -> ForecastPoint:
    """Inverse log-transform для одной точки. CI границы тоже exp()."""
    return ForecastPoint(
        date=p.date,
        value=float(np.exp(p.value)),
        ci_80=ConfidenceInterval(
            level=0.80,
            low=float(np.exp(p.ci_80.low)),
            high=float(np.exp(p.ci_80.high)),
        ),
        ci_95=ConfidenceInterval(
            level=0.95,
            low=float(np.exp(p.ci_95.low)),
            high=float(np.exp(p.ci_95.high)),
        ),
    )


# =============================================================================
# Internal — derived path
# =============================================================================


def _forecast_derived(
    asset: str,
    horizon: Horizon,
    method: ModelMethod,
    history_years: float,
    use_cache: bool,
) -> ForecastResult:
    if asset == "urals":
        brent_fc = _forecast_observable("brent", horizon, method, history_years, use_cache)
        result = derive_urals_forecast(brent_fc)
        return _attach_interpretation(result)

    if asset == "espo":
        brent_fc = _forecast_observable("brent", horizon, method, history_years, use_cache)
        result = derive_espo_forecast(brent_fc)
        return _attach_interpretation(result)

    if asset == "urals_minfin_blend":
        brent_fc = _forecast_observable("brent", horizon, method, history_years, use_cache)
        urals_fc = derive_urals_forecast(brent_fc)
        espo_fc = derive_espo_forecast(brent_fc)
        result = derive_minfin_blend_forecast(urals_fc, espo_fc)
        return _attach_interpretation(result)

    raise ValueError(f"unknown derived asset: {asset!r}")


def _attach_interpretation(result: ForecastResult) -> ForecastResult:
    return result.model_copy(update={
        "interpretation": generate_interpretation(result),
    })


# =============================================================================
# Internal — helpers
# =============================================================================


_HORIZON_RE = re.compile(r"^(\d+)\s*([dwmy])$", re.IGNORECASE)


def _parse_horizon(
    asset: str,
    raw: Union[str, Horizon],
) -> Union[Horizon, ForecastRefusal]:
    """Парсинг "1m" / "3m" / "6m" / "12m" / "18m" / "1y" / etc.

    1d/1w → ValueError (область дей-трейдинга, не наша).
    >= 18m → ForecastRefusal.
    """
    if isinstance(raw, Horizon):
        return raw

    if not isinstance(raw, str):
        raise TypeError(f"horizon must be str or Horizon, got {type(raw).__name__}")

    s = raw.strip().lower()
    m = _HORIZON_RE.match(s)
    if not m:
        raise ValueError(
            f"invalid horizon format: {raw!r}. Use '1m'/'3m'/'6m'/'12m'."
        )

    n = int(m.group(1))
    unit = m.group(2)

    # Convert to months
    if unit == "d":
        raise ValueError(
            "Сутки/недели — не наша область (это дей-трейдинг). "
            "Используй >= 1m. Если нужен 1d — RW + futures почти всегда лучшие, "
            "стат-модели не дают добавленной ценности."
        )
    if unit == "w":
        raise ValueError("Weekly horizons не поддерживаются (см. сообщение про '1d').")
    if unit == "y":
        n_months = n * 12
    else:  # 'm'
        n_months = n

    if n_months >= 18:
        return ForecastRefusal(
            asset=asset,
            requested_horizon_months=n_months,
            reason=(
                f"Точечный прогноз на {n_months} месяцев бесполезен — "
                "литература (Baumeister-Kilian, EIA STEO) показывает что "
                "стат-модели проигрывают сценарным подходам на горизонтах "
                ">=18m. Используй сценарные источники в RAG-корпусе."
            ),
        )

    if n_months not in {1, 3, 6, 12}:
        raise ValueError(
            f"horizon {n_months}m не поддерживается. "
            f"Используй один из 1m/3m/6m/12m."
        )

    return Horizon(f"{n_months}m")


def _default_method_for(asset: str, horizon: Horizon) -> ModelMethod:
    """Default method per horizon. Будет уточнено по бектесту в эксперименте."""
    return _DEFAULT_METHOD_BY_HORIZON[horizon]


def _build_model(method: ModelMethod) -> BaseForecaster:
    if method == ModelMethod.RANDOM_WALK:
        return RandomWalkForecaster()
    if method == ModelMethod.SARIMAX:
        return SARIMAXForecaster()
    if method == ModelMethod.XGBOOST:
        return XGBoostForecaster()
    if method == ModelMethod.ENSEMBLE:
        return EnsembleForecaster([
            SARIMAXForecaster(),
            XGBoostForecaster(),
        ])
    raise ValueError(f"unsupported method: {method}")


def _fetch_history(
    asset: str,
    *,
    since: pd.Timestamp,
    use_cache: bool,
) -> pd.Series:
    meta = get_asset(asset)
    src = meta.primary_source

    if src == DataSource.YFINANCE:
        return fetch_yfinance(asset, since=since, use_cache=use_cache)
    if src == DataSource.EIA:
        return fetch_eia_for_asset(asset, since=since, use_cache=use_cache)
    if src == DataSource.MOEX_ISS:
        return fetch_moex(asset, since=since, use_cache=use_cache)
    raise ValueError(
        f"asset {asset!r} primary_source={src.value!r} — unsupported in this fetcher router"
    )


__all__ = ["forecast"]
