"""RU/EN токенизация для BM25 sparse-retrieval (см. ADR-0027).

Наивный whitespace-BM25 плох на русском: богатая морфология
(нефть / нефти / нефтью / нефтяной) без лемматизации не схлопывается в один
терм, и лексический матч разваливается. Этот модуль:

  - токенизирует по unicode-словам (``\\w+`` ловит кириллицу + латиницу + цифры);
  - приводит к нижнему регистру;
  - лемматизирует кириллические токены через pymorphy3 (нефти → нефть);
  - выбрасывает RU/EN стоп-слова и одиночные буквы, но СОХРАНЯЕТ цифры —
    годы и числовые показатели критичны для финансовых/корпоративных запросов.

pymorphy3 опционален: если его нет, модуль молча деградирует до
lower + токенизация (это «минимум» из ТЗ) и логирует предупреждение один раз.
Корпус остаётся токенизируемым даже без словаря.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# Версия токенайзера — часть ключа дискового кэша sparse-индекса. Меняй при
# любой правке логики токенизации/стоп-слов, чтобы инвалидировать старый кэш.
TOKENIZER_VERSION = "v1-pymorphy3-lemma"

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")

# Компактные стоп-листы. BM25 через IDF и так занижает частотные термы, поэтому
# это полировка precision, а не несущая конструкция — держим списки маленькими,
# чтобы не выкидывать сигнал.
_RU_STOP = frozenset(
    """и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
    только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если
    уже или ни быть был него до вас уж вам ведь там потом себя ничего ей может они тут
    где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
    будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой
    тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой
    хоть после над больше тот через эти нас про всего них какая много разве три эту моя
    впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
    конечно всю между это её""".split()
)
_EN_STOP = frozenset(
    """a an the and or but if then else of to in on at by for with from as is are was
    were be been being this that these those it its he she they we you i them his her
    their our your my me him us do does did has have had not no yes so such than too
    very can will just about into over under again more most other some any all both
    each""".split()
)
_STOP = _RU_STOP | _EN_STOP

_morph = None
_morph_unavailable = False


def _get_morph():
    """Ленивый singleton pymorphy3.MorphAnalyzer. None — если pymorphy3 нет."""
    global _morph, _morph_unavailable
    if _morph is None and not _morph_unavailable:
        try:
            import pymorphy3

            _morph = pymorphy3.MorphAnalyzer()
        except Exception as exc:  # noqa: BLE001 — деградируем, не падаем
            _morph_unavailable = True
            logger.warning(
                "pymorphy3 недоступен (%s) — BM25 переходит на RU-токенизацию "
                "без лемматизации (минимум по ТЗ)",
                exc,
            )
    return _morph


@lru_cache(maxsize=200_000)
def _lemma(token: str) -> str:
    """Лемма кириллического токена. Кэш — корпус сильно повторяет термы (Zipf)."""
    morph = _get_morph()
    if morph is None:
        return token
    return morph.parse(token)[0].normal_form


def lemmatization_available() -> bool:
    """True, если pymorphy3 загрузился и лемматизация активна."""
    return _get_morph() is not None


def tokenize(text: str) -> list[str]:
    """Текст → нормализованные токены для BM25.

    Цифры сохраняются как есть; кириллица лемматизируется; одиночные буквы и
    стоп-слова отбрасываются.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if raw.isdigit():
            tokens.append(raw)  # годы, цены, объёмы — важный лексический сигнал
            continue
        if len(raw) < 2:
            continue  # одиночная буква = шум
        tok = _lemma(raw) if _CYRILLIC_RE.search(raw) else raw
        if tok in _STOP:
            continue
        tokens.append(tok)
    return tokens
