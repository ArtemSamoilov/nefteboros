"""Реестр активов: метаданные, источники, модели per asset.

Канонический список из ADR-0012 §«Активы».

Использование:

    from nefteboros.forecast.registry import ASSET_REGISTRY, get_asset

    meta = get_asset("brent")
    fetcher = meta.primary_source                     # DataSource.YFINANCE
    use_log = meta.log_transform                       # True для газовых
    available_methods = meta.available_methods         # set[ModelMethod]
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from nefteboros.forecast.schema import (
    AssetGroup,
    DataSource,
    Frequency,
    ModelMethod,
)


class AssetMeta(BaseModel):
    """Метаданные одного актива в реестре."""

    model_config = ConfigDict(frozen=True)

    asset_id: str
    display_name: str
    group: AssetGroup
    frequency: Frequency
    unit: str  # "USD/bbl", "USD/MMBtu", "RUB/1000m3", ...

    # Источники
    primary_source: DataSource
    primary_ticker: Optional[str] = None     # тикер в primary источнике
    secondary_source: Optional[DataSource] = None
    secondary_ticker: Optional[str] = None

    # Модельные особенности
    log_transform: bool = False              # True для газовых рядов с экстремумами
    spread_against: Optional[str] = None     # "brent" → asset = brent + spread_model
    derived_from: Optional[list[tuple[str, float]]] = None  # [("urals", 0.78), ("espo", 0.22)]

    available_methods: set[ModelMethod] = Field(
        default_factory=lambda: {
            ModelMethod.RANDOM_WALK,
            ModelMethod.SARIMAX,
            ModelMethod.XGBOOST,
            ModelMethod.ENSEMBLE,
        }
    )

    # Произвольные заметки для документации/отладки
    notes: Optional[str] = None


# =============================================================================
# Реестр (канонический список из ADR-0012)
# =============================================================================


def _all_methods() -> set[ModelMethod]:
    return {
        ModelMethod.RANDOM_WALK,
        ModelMethod.SARIMAX,
        ModelMethod.XGBOOST,
        ModelMethod.ENSEMBLE,
    }


def _no_xgb_methods() -> set[ModelMethod]:
    """Без XGBoost — для активов, где данных недостаточно для conformal."""
    return {
        ModelMethod.RANDOM_WALK,
        ModelMethod.SARIMAX,
        ModelMethod.ENSEMBLE,
    }


ASSET_REGISTRY: dict[str, AssetMeta] = {
    # ----- Глобальная нефть -----
    "brent": AssetMeta(
        asset_id="brent",
        display_name="Brent Crude (ICE front-month)",
        group=AssetGroup.OIL_GLOBAL,
        frequency=Frequency.DAILY,
        unit="USD/bbl",
        primary_source=DataSource.YFINANCE,
        primary_ticker="BZ=F",
        secondary_source=DataSource.EIA,
        secondary_ticker="PET.RBRTE.D",
        available_methods=_all_methods(),
    ),
    "wti": AssetMeta(
        asset_id="wti",
        display_name="WTI Crude (NYMEX front-month)",
        group=AssetGroup.OIL_GLOBAL,
        frequency=Frequency.DAILY,
        unit="USD/bbl",
        primary_source=DataSource.YFINANCE,
        primary_ticker="CL=F",
        secondary_source=DataSource.EIA,
        secondary_ticker="PET.RWTC.D",
        available_methods=_all_methods(),
        notes="Моделируется напрямую, не через спред с Brent — избегаем каскадирования ошибок.",
    ),
    # ----- Российская нефть -----
    # NB: urals, espo, urals_minfin_blend — DERIVED (см. ADR-0012, strict separation).
    # Они не имеют собственных моделей. available_methods = "пустые" для модельной
    # части — в forecast.api для derived-актива base-прогноз делается на Brent
    # выбранным методом, потом сверху накладывается spread/blend layer.
    # CI расширяется на spread-uncertainty.
    "urals": AssetMeta(
        asset_id="urals",
        display_name="Urals Crude (CFR Mediterranean) — derived from Brent + spread",
        group=AssetGroup.OIL_RUSSIAN,
        frequency=Frequency.DAILY,
        unit="USD/bbl",
        primary_source=DataSource.DERIVED,
        primary_ticker=None,
        spread_against="brent",
        available_methods=_all_methods(),  # доступны как методы базового Brent-прогноза
        notes=(
            "DERIVED. Прямых daily-цен Urals 5y в открытых источниках нет (investing.com — "
            "Feb 2025 cutoff). Получается как Brent_forecast(method) − spread_curr(target_date), "
            "где spread берётся из spread_schedule.py (4 режима: pre_war/war_shock/cap_phase_1/"
            "cap_phase_2; источник — Bruegel WP 32/2025 + Минэк). Бектест-метрики не вычисляются "
            "самостоятельно — модели обучаются на Brent."
        ),
    ),
    "espo": AssetMeta(
        asset_id="espo",
        display_name="ESPO Blend (Pacific FOB Kozmino) — derived from Brent + spread",
        group=AssetGroup.OIL_RUSSIAN,
        frequency=Frequency.DAILY,
        unit="USD/bbl",
        primary_source=DataSource.DERIVED,
        primary_ticker=None,
        spread_against="brent",
        available_methods=_all_methods(),
        notes=(
            "DERIVED. Light sweet (34.7°API), восточный экспорт. Аналогично Urals, "
            "но spread меньше (Asian premium — ESPO исторически ближе к Brent чем Urals)."
        ),
    ),
    "urals_minfin_blend": AssetMeta(
        asset_id="urals_minfin_blend",
        display_name="Urals/ESPO Blend (Минфин РФ — НДПИ-формула, piecewise)",
        group=AssetGroup.OIL_RUSSIAN,
        frequency=Frequency.DAILY,
        unit="USD/bbl",
        primary_source=DataSource.DERIVED,
        primary_ticker=None,
        derived_from=[("urals", 0.78), ("espo", 0.22)],
        available_methods=_all_methods(),
        notes=(
            "DERIVED, piecewise. До 2025-01 = Urals (Минфин считал НДПИ только по Urals). "
            "С 2025-01 = 0.78 × Urals_FOB(Primorsk+Novorossiysk) + 0.22 × ESPO_FOB_Kozmino "
            "(новая формула 2025+). Прогноз — convolution прогнозов компонент."
        ),
    ),
    # ----- Глобальный газ -----
    "henry_hub": AssetMeta(
        asset_id="henry_hub",
        display_name="Henry Hub Natural Gas (NYMEX front-month)",
        group=AssetGroup.GAS_GLOBAL,
        frequency=Frequency.DAILY,
        unit="USD/MMBtu",
        primary_source=DataSource.YFINANCE,
        primary_ticker="NG=F",
        secondary_source=DataSource.EIA,
        secondary_ticker="NG.RNGWHHD.D",
        log_transform=True,
        available_methods=_all_methods(),
    ),
    "ttf": AssetMeta(
        asset_id="ttf",
        display_name="Dutch TTF Natural Gas (ICE Endex front-month)",
        group=AssetGroup.GAS_GLOBAL,
        frequency=Frequency.DAILY,
        unit="EUR/MWh",
        primary_source=DataSource.YFINANCE,
        primary_ticker="TTF=F",
        log_transform=True,
        available_methods=_all_methods(),
        notes="Экстремумы 2022 ×10 от нормы — log-transform обязателен; coverage CI просядет на этом периоде.",
    ),
    # JKM (Asian LNG) перенесён в P2 — investing.com отдаёт только последний месяц
    # через __NEXT_DATA__, AJAX-endpoint требует реверса. Для Asian gas вопросов
    # в interpret.py используется TTF как ближайший proxy (corr ≈ 0.85).

    # ----- Российский нефтегазовый proxy (через MOEX ISS) -----
    # Прямых daily-цен внутрироссийского газа в открытых источниках нет:
    #   - СПбМТСБ — daily на сайте видно, но скачать только через коммерческий канал
    #     (sales@spimex.com / платный API).
    #   - CBR gas.xls — фид остановлен 25.03.2022 (квартальные данные обрываются на IV кв.2021).
    #   - ФТС/Росстат — IP-блок / SSL EOF с не-РФ.
    # Замена: рыночный proxy российского нефтегазового сектора через MOEX ISS API
    # (публичный, без VPN). Это не цена газа в RUB/1000m3, а рыночная оценка сектора —
    # релевантно для роли аналитика Сбера, оценивающего кредитоспособность эмитентов.
    "moexog": AssetMeta(
        asset_id="moexog",
        display_name="MOEX Oil & Gas Index",
        group=AssetGroup.RUSSIAN_ENERGY_PROXY,
        frequency=Frequency.DAILY,
        unit="pts (RUB-weighted)",
        primary_source=DataSource.MOEX_ISS,
        primary_ticker="MOEXOG",
        log_transform=False,
        available_methods=_all_methods(),
        notes=(
            "Сводный отраслевой индекс — Газпром, Новатэк, Роснефть, Лукойл, "
            "Татнефть и др. с весами по капитализации. Главный proxy состояния "
            "российского нефтегазового сектора."
        ),
    ),
    "gazp": AssetMeta(
        asset_id="gazp",
        display_name="Газпром (TQBR)",
        group=AssetGroup.RUSSIAN_ENERGY_PROXY,
        frequency=Frequency.DAILY,
        unit="RUB",
        primary_source=DataSource.MOEX_ISS,
        primary_ticker="GAZP",
        log_transform=False,
        available_methods=_all_methods(),
        notes=(
            "Стоимость акций ПАО «Газпром» — proxy финансовой устойчивости "
            "крупнейшего российского газового монополиста."
        ),
    ),
    "nvtk": AssetMeta(
        asset_id="nvtk",
        display_name="Новатэк (TQBR)",
        group=AssetGroup.RUSSIAN_ENERGY_PROXY,
        frequency=Frequency.DAILY,
        unit="RUB",
        primary_source=DataSource.MOEX_ISS,
        primary_ticker="NVTK",
        log_transform=False,
        available_methods=_all_methods(),
        notes=(
            "Стоимость акций ПАО «Новатэк» — proxy для российского СПГ-сектора "
            "(Ямал СПГ, Арктик СПГ-2)."
        ),
    ),
    # ----- P1 -----
    "opec_basket": AssetMeta(
        asset_id="opec_basket",
        display_name="OPEC Reference Basket",
        group=AssetGroup.OIL_GLOBAL,
        frequency=Frequency.DAILY,
        unit="USD/bbl",
        primary_source=DataSource.OPEC,
        primary_ticker="opec_basket_xml_feed",
        available_methods=_all_methods(),
        notes="P1 — добавляется в PR1 если хватает времени; иначе отдельный мини-PR.",
    ),
}


# =============================================================================
# Invariant: AssetID Literal в schema.py должен совпадать с ключами реестра.
# Проверяется при импорте модуля — ловит расхождение источников истины.
# =============================================================================


def _check_registry_matches_schema() -> None:
    """Сверить ключи ASSET_REGISTRY с typing.get_args(AssetID).

    AssetID — публичный контракт (используется в API, в типах агента-tool'а
    Ouroboros, в IDE-autocomplete). ASSET_REGISTRY — runtime-источник
    метаданных. Если расходятся — кто-то добавил/удалил актив в одном месте
    и забыл другое.
    """
    import typing

    from nefteboros.forecast.schema import AssetID

    expected: set[str] = set(typing.get_args(AssetID))
    actual: set[str] = set(ASSET_REGISTRY.keys())
    if expected != actual:
        missing = expected - actual
        extra = actual - expected
        raise RuntimeError(
            "ASSET_REGISTRY и schema.AssetID Literal разошлись:\n"
            f"  в Literal, но не в registry: {sorted(missing) or '∅'}\n"
            f"  в registry, но не в Literal: {sorted(extra) or '∅'}\n"
            "Поправь schema.AssetID и/или registry.ASSET_REGISTRY."
        )


_check_registry_matches_schema()


# =============================================================================
# Lookup helpers
# =============================================================================


def get_asset(asset_id: str) -> AssetMeta:
    """Получить метаданные актива по ID. Бросает KeyError если не найден."""
    if asset_id not in ASSET_REGISTRY:
        valid = ", ".join(sorted(ASSET_REGISTRY.keys()))
        raise KeyError(
            f"Unknown asset_id={asset_id!r}. "
            f"Valid: {valid}"
        )
    return ASSET_REGISTRY[asset_id]


def list_assets(*, group: Optional[AssetGroup] = None) -> list[str]:
    """Перечислить asset_id'ы; опционально — фильтр по группе."""
    if group is None:
        return list(ASSET_REGISTRY.keys())
    return [aid for aid, meta in ASSET_REGISTRY.items() if meta.group == group]


__all__ = ["AssetMeta", "ASSET_REGISTRY", "get_asset", "list_assets"]
