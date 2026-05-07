"""Lang detection для Brave Search.

Простой детектор по доле кириллицы. langdetect/fasttext шумит на
коротких user-query'ях и тянет тяжёлые deps; нам нужна бинарная
классификация — RU vs EN.

Назначение — пробрасывать в Brave параметры `search_lang/country/ui_lang`
так, чтобы RU-запрос ловил RU-источники (Vedomosti/Kommersant/RBC/
Interfax/TASS), а EN-запрос — EN-источники (Reuters/Bloomberg/FT/
Argus/Platts). Без этого Brave дефолтно тянет EN и RU-tier1 теряется.
"""
from __future__ import annotations

# Доля кириллических букв в query, начиная с которой считаем RU.
# 0.3 потому что бывают смешанные query типа "Новатэк LNG strategy" —
# их классифицируем по преобладающему языку первой части.
_CYRILLIC_THRESHOLD = 0.3


def detect_lang(query: str) -> str:
    """Returns 'ru' или 'en'. На пустом/нелитеральном — 'en'."""
    letters = [c for c in (query or "") if c.isalpha()]
    if not letters:
        return "en"
    cyrillic = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    return "ru" if cyrillic / len(letters) >= _CYRILLIC_THRESHOLD else "en"


def brave_params_for_lang(lang: str) -> dict[str, str]:
    """Brave query params для языка.

    Spec: https://api.search.brave.com/app/documentation/web-search/query
    - search_lang: язык контента в результатах (ru | en | ...)
    - country: 2-letter ISO для гео-предпочтения
    - ui_lang: lang_country формат для интерфейса
    """
    if lang == "ru":
        return {"search_lang": "ru", "country": "RU", "ui_lang": "ru-RU"}
    return {"search_lang": "en", "country": "US", "ui_lang": "en-US"}


__all__ = ["detect_lang", "brave_params_for_lang"]
