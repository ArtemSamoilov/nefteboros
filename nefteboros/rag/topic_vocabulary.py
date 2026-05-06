"""Закрытый словарь topic-tags для RAG-чанков.

5 осей × 22 значения. Каждый чанк может иметь 0-3 значения на каждой оси.
Изменения в словаре требуют переcборки эмбеддингов и пересмотра eval-датасета.

См. ADR-0011 (chunking + tagging).
"""
from __future__ import annotations

from typing import Final, Literal, get_args

# -----------------------------------------------------------------------------
# Оси и допустимые значения
# -----------------------------------------------------------------------------

EnergyTag = Literal["oil", "gas", "lng", "oil_products"]
MarketAspectTag = Literal[
    "supply",
    "demand",
    "prices",
    "inventories",
    "trade",
    "infrastructure",
]
GeopoliticsTag = Literal["sanctions", "conflicts", "energy_security"]
FinanceTag = Literal[
    "corporate_finance",
    "government_finance",
    "strategy",
    "forecast",
]
RegionTag = Literal["russia", "europe", "us", "middle_east", "asia"]

VOCABULARY: Final[dict[str, tuple[str, ...]]] = {
    "energy": get_args(EnergyTag),
    "market_aspect": get_args(MarketAspectTag),
    "geopolitics": get_args(GeopoliticsTag),
    "finance": get_args(FinanceTag),
    "region": get_args(RegionTag),
}

# -----------------------------------------------------------------------------
# Описания значений — используются в LLM-промпте при tagging'е
# -----------------------------------------------------------------------------

TAG_DESCRIPTIONS: Final[dict[str, str]] = {
    # energy
    "oil": "Сырая нефть и связанные темы (Brent, WTI, Urals, добыча/потребление)",
    "gas": "Природный газ (трубопроводный, спот-цены TTF/Henry Hub, добыча, потребление)",
    "lng": "Сжиженный природный газ (СПГ-проекты, экспорт, торговля, JKM)",
    "oil_products": "Нефтепродукты (бензин, дизель, авиакеросин), маржи нефтепереработки",
    # market_aspect
    "supply": "Производство, добыча, разработка месторождений, мощности",
    "demand": "Потребление, спрос, прогнозы потребления",
    "prices": "Цены, бенчмарки, спот, форварды, ценообразование",
    "inventories": "Запасы (commercial, strategic, OECD, US crude inventories, SPR)",
    "trade": "Экспорт/импорт, торговые потоки, направления поставок, СПГ-trade",
    "infrastructure": "Трубопроводы, терминалы, заводы СПГ, NGV, refineries, storage",
    # geopolitics
    "sanctions": "Санкции, price cap, embargo, OFAC/EU regulations",
    "conflicts": "Военные/политические конфликты с энергетическим эффектом",
    "energy_security": "Энергобезопасность, диверсификация, замещение источников",
    # finance
    "corporate_finance": "P&L, баланс, cash flow, дивиденды, capex, M&A корпораций",
    "government_finance": "Бюджет, налоги (НДПИ/НДД), нефтегаздоходы, ФНБ",
    "strategy": "Долгосрочные планы, стратегии, energy transition сценарии",
    "forecast": "Прогнозы (цен, добычи, спроса) на горизонте мес-десятилетия",
    # region
    "russia": "Россия и её энергетический сектор",
    "europe": "Европейский Союз, UK — потребление/импорт/политика",
    "us": "США — добыча, потребление, политика, expoort СПГ",
    "middle_east": "Ближний Восток (Саудовская Аравия, ОАЭ, Иран, Ирак, Кувейт, Катар)",
    "asia": "Азия (Китай, Индия, Япония, Южная Корея, Юго-Восточная Азия)",
}

# Sanity-проверка: каждое значение оси имеет описание
_missing_descriptions = [
    v for axis_values in VOCABULARY.values() for v in axis_values if v not in TAG_DESCRIPTIONS
]
assert not _missing_descriptions, (
    f"TAG_DESCRIPTIONS missing entries for: {_missing_descriptions}"
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def all_axes() -> list[str]:
    return list(VOCABULARY.keys())


def values_for(axis: str) -> tuple[str, ...]:
    if axis not in VOCABULARY:
        raise ValueError(f"Unknown axis {axis!r}. Available: {all_axes()}")
    return VOCABULARY[axis]


def is_valid(axis: str, value: str) -> bool:
    return value in VOCABULARY.get(axis, ())


def filter_valid(axis: str, values: list[str]) -> list[str]:
    """Удаляет значения, которых нет в словаре. Используется при парсинге LLM-ответа."""
    allowed = set(VOCABULARY.get(axis, ()))
    return [v for v in values if v in allowed]
