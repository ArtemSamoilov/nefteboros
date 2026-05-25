"""Детекция состояний геополитических драйверов из tier-1 веб-новостей (ADR-0028).

Конвейер на ОДИН драйвер:
  tier-1 Brave-поиск → LLM классифицирует КАЖДЫЙ источник в закрытый enum
  (ровно ключи DRIVERS) + дата события + уверенность → правило ≥2 источников
  применяется В КОДЕ (детерминированно, аудируемо), НЕ в LLM.

Разделение ответственности (как в этапе 1): LLM судит ФАКТ («этот источник
говорит, что Ормуз частично открыт»), а РЕШЕНИЕ менять ли состояние принимает
детерминированный код:
  - смена состояния ТОЛЬКО при ≥2 РАЗЛИЧНЫХ tier-1 хостах за одно новое состояние;
  - конфликт (≥2 разных не-prior состояния по ≥2 источника) → `disputed`, не меняем;
  - иначе остаётся prior (старое) состояние.

Ловушки (ТЗ): жёлтая пресса отсекается tier-фильтром (tier_filter="tier1");
старая новость ≠ изменение — `event_date` старее `max_event_age_days` отбрасывается.

LLM-классификатор повторяет идиому `rag/query_classifier.py` (get_chat_model
hydra/kimi, temperature=0, JSON-ответ, парс с зачисткой markdown). Для тестов
подменяется любой объект с `classify_sources(driver, hits) -> list[SourceVerdict]`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Protocol

from nefteboros.forecast.web_flags.models import (
    DRIVER_NAMES,
    DriverDetection,
    FlagSource,
    is_valid_state,
    valid_states,
)
from nefteboros.search import SearchHit, WebSearcher

logger = logging.getLogger(__name__)


# Минимум различных tier-1 источников, чтобы СМЕНИТЬ состояние драйвера (ТЗ §Детекция).
MIN_TIER1_SOURCES: int = 2

DEFAULT_K: int = 6
DEFAULT_FRESHNESS: str = "pw"  # past week — свежесть важна (старая новость ≠ изменение)
DEFAULT_MAX_EVENT_AGE_DAYS: int = 45


# Целевой tier-1 запрос на каждый драйвер (англоязычный — tier-1 EN-источники
# доминируют в выдаче; lang-детект Brave сам подмешает RU при кириллице).
DRIVER_QUERIES: dict[str, str] = {
    "hormuz": "Strait of Hormuz oil tanker shipping closure reopen latest",
    "iran": "Iran oil sanctions exports enforcement waiver latest",
    "opec_plus": "OPEC+ oil production quota decision unwind cuts latest",
    "russia_cap": "Russia oil price cap G7 EU enforcement level latest",
    "china_demand": "China crude oil demand imports outlook latest",
}


# Короткие описания состояний для LLM (по семантике DRIVERS/FLAGS_DECOMPOSITION
# этапа 1). Знак влияния на баланс предложения — в самой цепочке, не здесь.
DRIVER_STATE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "hormuz": {
        "blocked": "Ормузский пролив заблокирован/судоходство критически нарушено (текущий кризис, нефть ушла с рынка)",
        "partial_reopen": "Частичное возобновление: подписан MOU/деэскалация, часть поставок вернулась",
        "full_reopen": "Полное возобновление судоходства, кризис разрешён",
        "partial_closure": "Дополнительное ужесточение/частичное закрытие сверх текущего (ещё нефть с рынка)",
        "full_closure": "Полная блокада пролива (катастрофический сценарий)",
    },
    "iran": {
        "maximum_pressure_active": "Действует максимальное санкционное давление на иранский экспорт (текущее)",
        "partial_lift": "Частичное снятие санкций / возврат части иранского экспорта",
        "full_lift": "Полное снятие санкций, иранский экспорт нормализован",
        "further_tightening": "Дополнительное ужесточение санкций сверх текущего",
    },
    "opec_plus": {
        "gradual": "Плавный unwind добровольных сокращений (+~206k bpd/мес, текущее)",
        "accelerated": "Ускоренный возврат добычи ОПЕК+ на рынок",
        "extended": "Продление/углубление сокращений (защита цен, меньше нефти)",
    },
    "russia_cap": {
        "active": "Ценовой потолок G7/ЕС действует на текущем уровне (текущее)",
        "tightened_dynamic": "Ужесточение enforcement/динамический потолок (ценовой эффект, не объём)",
        "removed": "Потолок снят/обходится, российский экспорт нормализуется (больше нефти)",
    },
    "china_demand": {
        "base": "Спрос Китая в базовом тренде (+~0.2 mbpd y/y, текущее)",
        "weak": "Спрос Китая слабеет (price-induced softening, профицит на рынке)",
        "strong": "Спрос Китая сильнее ожиданий (дефицит на рынке)",
    },
}


@dataclass(frozen=True)
class SourceVerdict:
    """Вердикт LLM по ОДНОМУ источнику (выровнен по индексу с hits).

    `state == "none"` ⇒ источник не относится к драйверу / нет ясного состояния.
    """

    state: str
    confidence: float = 0.0
    event_date: Optional[str] = None
    quote: str = ""


class SupportsClassify(Protocol):
    def classify_sources(self, driver: str, hits: list[SearchHit]) -> list["SourceVerdict"]: ...


# =============================================================================
# LLM-классификатор источников в закрытый enum состояний
# =============================================================================


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_DEFAULT_MODEL = "kimi-k2p6"
_RETRY_ATTEMPTS = 3


def _build_states_block(driver: str) -> str:
    lines = []
    for st, desc in DRIVER_STATE_DESCRIPTIONS.get(driver, {}).items():
        lines.append(f"- {st}: {desc}")
    return "\n".join(lines)


def _system_prompt(driver: str) -> str:
    return f"""Ты — классификатор новостей по геополитическому драйверу нефтяного рынка «{driver}».

Тебе дают пронумерованные источники (заголовок, домен, дата, фрагмент). Для КАЖДОГО
источника определи, о каком состоянии драйвера он сообщает, из ЗАКРЫТОГО списка:

{_build_states_block(driver)}

Правила:
1. Возвращай состояние ТОЛЬКО из списка выше. Если источник не про этот драйвер
   или состояние неясно — верни "none".
2. Суди ФАКТ из текста источника, не домысливай. Нужна явная опора в заголовке/фрагменте.
3. Извлеки дату СОБЫТИЯ (event_date, YYYY-MM-DD или null если не указана) — старая
   новость о давнем событии НЕ является изменением.
4. confidence: 0.0–1.0 — насколько источник прямо подтверждает состояние.
5. Возвращай ТОЛЬКО JSON без markdown:
   {{"verdicts": [{{"i": 0, "state": "<state|none>", "event_date": "YYYY-MM-DD|null", "confidence": 0.0, "quote": "<короткая цитата>"}}]}}
""".strip()


def _format_hits(hits: list[SearchHit]) -> str:
    blocks = []
    for i, h in enumerate(hits):
        when = h.published or h.age or "дата?"
        blocks.append(
            f"[{i}] {h.title}\n    домен: {h.hostname} | дата: {when}\n    {h.snippet}"
        )
    return "\n\n".join(blocks)


def _parse_verdicts(raw: str, n_hits: int) -> list[SourceVerdict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    m = _JSON_RE.search(raw)
    if not m:
        raise ValueError(f"No JSON in classifier response: {raw[:200]!r}")
    parsed = json.loads(m.group(0))
    verdicts: list[SourceVerdict] = [SourceVerdict(state="none") for _ in range(n_hits)]
    for v in parsed.get("verdicts", []) or []:
        try:
            i = int(v.get("i"))
        except (TypeError, ValueError):
            continue
        if not (0 <= i < n_hits):
            continue
        ed = v.get("event_date")
        if isinstance(ed, str) and ed.lower() in ("null", "none", ""):
            ed = None
        try:
            conf = float(v.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        verdicts[i] = SourceVerdict(
            state=str(v.get("state") or "none"),
            confidence=max(0.0, min(1.0, conf)),
            event_date=ed,
            quote=str(v.get("quote") or "")[:300],
        )
    return verdicts


class DriverStateClassifier:
    """LLM-классификатор источников → состояния драйвера (закрытый enum).

    Идиома `rag/query_classifier.py`: дешёвая быстрая модель, temperature=0,
    JSON-ответ. Lazy LLM — конструктор не создаёт клиента, чтобы импорт модуля
    не требовал ключей (важно для unit-тестов с подменой).
    """

    def __init__(self, model: str = _DEFAULT_MODEL, *, provider: str = "hydra") -> None:
        self.model_name = model
        self.provider = provider
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            from nefteboros.llm import get_chat_model

            self._llm = get_chat_model(provider=self.provider, model=self.model_name, temperature=0)
        return self._llm

    def classify_sources(self, driver: str, hits: list[SearchHit]) -> list[SourceVerdict]:
        if not hits:
            return []
        messages = [
            {"role": "system", "content": _system_prompt(driver)},
            {"role": "user", "content": _format_hits(hits)},
        ]
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                resp = self.llm.invoke(messages)
                content = resp.content if hasattr(resp, "content") else str(resp)
                return _parse_verdicts(content, len(hits))
            except Exception as e:  # noqa: BLE001
                if attempt >= _RETRY_ATTEMPTS:
                    logger.warning("classify_sources failed for %r: %s", driver, e)
                    return [SourceVerdict(state="none") for _ in hits]
        return [SourceVerdict(state="none") for _ in hits]


# =============================================================================
# Оркестратор детекции — применяет правило ≥2 tier-1 источников
# =============================================================================


def _parse_event_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


class FlagDetector:
    """Оркестрирует детекцию всех драйверов и применяет ≥2-source правило.

    Зависимости инъектируются (searcher, classifier) — для unit-тестов
    подставляются fake без сети и без LLM.
    """

    def __init__(
        self,
        searcher: Optional[WebSearcher] = None,
        classifier: Optional[SupportsClassify] = None,
        *,
        k: int = DEFAULT_K,
        freshness: str = DEFAULT_FRESHNESS,
        max_event_age_days: int = DEFAULT_MAX_EVENT_AGE_DAYS,
        min_sources: int = MIN_TIER1_SOURCES,
    ) -> None:
        self.searcher = searcher if searcher is not None else WebSearcher()
        self.classifier = classifier if classifier is not None else DriverStateClassifier()
        self.k = k
        self.freshness = freshness
        self.max_event_age_days = max_event_age_days
        self.min_sources = min_sources

    def detect_driver(
        self,
        driver: str,
        prior_state: str,
        *,
        as_of: Optional[date] = None,
    ) -> DriverDetection:
        if driver not in DRIVER_NAMES:
            raise ValueError(f"Unknown driver {driver!r}. Valid: {DRIVER_NAMES}.")
        as_of = as_of or datetime.now(timezone.utc).date()

        query = DRIVER_QUERIES.get(driver, driver)
        hits = self.searcher.search(query, k=self.k, freshness=self.freshness, tier_filter="tier1")
        verdicts = self.classifier.classify_sources(driver, hits)

        # Сгруппировать поддержку по состоянию: только tier-1, валидное состояние,
        # не "none", не устаревшее событие, различные хосты.
        support: dict[str, dict[str, FlagSource]] = {}  # state -> {hostname: FlagSource}
        for hit, verdict in zip(hits, verdicts):
            state = verdict.state
            if state == "none" or not is_valid_state(driver, state):
                continue
            if hit.tier != "tier1":
                continue
            if self._is_stale(verdict.event_date, as_of):
                continue
            src = FlagSource(
                driver=driver,
                state=state,
                hostname=hit.hostname,
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
                tier=hit.tier,
                published=hit.published,
                event_date=verdict.event_date,
                confidence=verdict.confidence,
            )
            # один хост учитывается один раз за состояние (берём с макс. confidence)
            bucket = support.setdefault(state, {})
            prev = bucket.get(hit.hostname)
            if prev is None or src.confidence > prev.confidence:
                bucket[hit.hostname] = src

        # Кандидаты на СМЕНУ: не-prior состояния с ≥ min_sources различных хостов.
        candidates = {
            st: list(by_host.values())
            for st, by_host in support.items()
            if st != prior_state and len(by_host) >= self.min_sources
        }

        all_sources: list[FlagSource] = [s for by_host in support.values() for s in by_host.values()]

        if len(candidates) >= 2:
            # Конфликт: несколько подтверждённых разных состояний — не меняем.
            return DriverDetection(
                driver=driver,
                prior_state=prior_state,
                detected_state=prior_state,
                changed=False,
                disputed=True,
                confidence=0.0,
                reason=(
                    "Конфликт источников: ≥2 tier-1 подтверждают разные состояния "
                    f"({', '.join(sorted(candidates))}) — состояние не меняется (спорно)."
                ),
                sources=all_sources,
            )

        if len(candidates) == 1:
            new_state, srcs = next(iter(candidates.items()))
            conf = sum(s.confidence for s in srcs) / len(srcs)
            return DriverDetection(
                driver=driver,
                prior_state=prior_state,
                detected_state=new_state,
                changed=True,
                disputed=False,
                confidence=conf,
                reason=(
                    f"{len(srcs)} tier-1 источника подтверждают переход "
                    f"{prior_state} → {new_state}."
                ),
                sources=all_sources,
            )

        # Подтверждённой смены нет — остаётся prior.
        return DriverDetection(
            driver=driver,
            prior_state=prior_state,
            detected_state=prior_state,
            changed=False,
            disputed=False,
            confidence=0.0,
            reason=(
                f"Нет ≥{self.min_sources} tier-1 источников за новое состояние — "
                f"остаётся {prior_state}."
            ),
            sources=all_sources,
        )

    def detect_all(self, prior_flag_states: dict[str, str]) -> list[DriverDetection]:
        """Детекция по всем драйверам относительно prior-состояний."""
        as_of = datetime.now(timezone.utc).date()
        out: list[DriverDetection] = []
        for driver in DRIVER_NAMES:
            prior = prior_flag_states.get(driver) or _seed_state(driver)
            out.append(self.detect_driver(driver, prior, as_of=as_of))
        return out

    def _is_stale(self, event_date_raw: Optional[str], as_of: date) -> bool:
        """Старая новость ≠ изменение. Непарсящаяся дата НЕ считается устаревшей."""
        ed = _parse_event_date(event_date_raw)
        if ed is None:
            return False
        return (as_of - ed).days > self.max_event_age_days


def _seed_state(driver: str) -> str:
    from nefteboros.forecast.scenarios import DRIVER_BASE_STATES

    return DRIVER_BASE_STATES[driver]


__all__ = [
    "MIN_TIER1_SOURCES",
    "DRIVER_QUERIES",
    "DRIVER_STATE_DESCRIPTIONS",
    "SourceVerdict",
    "SupportsClassify",
    "DriverStateClassifier",
    "FlagDetector",
]
