"""Query intent classifier — pre-retrieval этап.

Принимает пользовательский запрос, возвращает topic-tags по тому же
закрытому словарю что и chunks (см. topic_vocabulary.py). Ретривер
использует их для boost/filter chunks с matching tags.

Логика похожа на tagger.py, но с другим промптом и без сохранения
в storage. Lazy-loaded LLM (singleton).

Производительность: ~1-3 сек на запрос через kimi-k2p6 на HydraGPT.
Кэшировать классификацию по lower-cased query — backlog (см. ADR-0016).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any

from nefteboros.llm import get_chat_model
from nefteboros.rag.schema import TopicTags
from nefteboros.rag.topic_vocabulary import (
    TAG_DESCRIPTIONS,
    VOCABULARY,
    filter_valid,
)

logger = logging.getLogger(__name__)


# Document types из manifest.yml — для filter `where={"type": {"$in": [...]}}` в Chroma.
# Описания нужны LLM-классификатору чтобы понять, какой тип искать под запрос.
DOC_TYPE_DESCRIPTIONS: dict[str, str] = {
    "annual_report": "Корпоративный годовой отчёт компании (Газпром AR, Лукойл AR, Новатэк AR и т.п.) — стратегия, операционка, ESG, корпоративное управление",
    "financial_report": "Финансовая отчётность компании по МСФО или РСБУ — детальный P&L, баланс, cash flow, примечания",
    "government_strategy": "Государственный стратегический документ (Энергостратегия РФ-2050) — целевые показатели, этапы, политика",
    "government_macro_forecast": "Макроэкономический прогноз правительства (Минэк СЭР) — ВВП, инфляция, цены нефти в основе бюджета",
    "institutional_forecast": "Долгосрочный прогноз институции (OPEC WOO, IEA Oil/Gas, ИНЭИ) — балансы спрос/предложение на 5-25 лет",
    "institutional_yearbook": "Годовой отчёт OPEC/IEA с обзором рынка за год",
    "statistical_reference": "Reference книга с историческими сериями (EI Statistical Review, OPEC ASB) — таблицы цифр по странам/энергоносителям за десятилетия",
    "industry_yearbook": "Отраслевой годовой отчёт (GIIGNL World LNG Report) — статистика и торговля СПГ",
    "market_digest": "Сводный аналитический digest сравнивающий несколько источников (IEF Comparative Analysis) — выжимки MOMR/STEO/OMR",
    "market_report": "Свежий operational отчёт (OPEC MOMR, EIA STEO, IEA Gas Market Quarterly) — балансы, цены за месяц/квартал",
    "research_briefing": "Тематический analytical brief (CRS reports, government research) — узкая тема (Iran sanctions, Hormuz)",
    "think_tank_paper": "Working paper think tank (Bruegel, CSIS) — глубокий аналитический разбор политики/санкций",
}

DEFAULT_MODEL = "kimi-k2p6"
RETRY_ATTEMPTS = 3
RETRY_DELAY_SEC = 1.5


def _build_vocabulary_block() -> str:
    lines = []
    for axis, values in VOCABULARY.items():
        lines.append(f"\n## {axis}")
        for v in values:
            lines.append(f"- {v}: {TAG_DESCRIPTIONS[v]}")
    return "\n".join(lines)


def _build_doc_type_block() -> str:
    return "\n".join(f"- {t}: {d}" for t, d in DOC_TYPE_DESCRIPTIONS.items())


DOC_TYPE_PROMPT = f"""Ты — классификатор пользовательских вопросов по типу источника, который ответит на запрос лучше всего.

Закрытый список типов документов в нашем нефтегазовом корпусе:

{_build_doc_type_block()}

Правила:
1. Верни **1-2 типа** в порядке убывания релевантности — только те, без которых ответ невозможен.
2. Если запрос — общий или у тебя есть сомнения — верни пустой массив [], не выбирай «на всякий случай».
3. Будь **строгим к точности типа**:
   - «cash flow / P&L / финансовая отчётность по МСФО» → ТОЛЬКО `financial_report` (НЕ `annual_report`)
   - «дивиденды, capex, операционные показатели компании» → ТОЛЬКО `annual_report`
   - «текущие цены / квоты / месячный обзор» → ТОЛЬКО `market_report` (или `market_digest`)
   - «целевые показатели правительства РФ» → ТОЛЬКО `government_strategy`
   - «исторические серии данных по странам/энергоносителям» → ТОЛЬКО `statistical_reference`
   - «прогнозы рынка нефти/газа на 5-25 лет» → ТОЛЬКО `institutional_forecast`
4. Возвращай **только JSON** без пояснений: {{"types": [...]}}
""".strip()


SYSTEM_PROMPT = f"""Ты — классификатор пользовательских вопросов в нефтегазовом домене.
Твоя задача — определить, какие topic-теги релевантны для поиска ответа,
по тому же закрытому словарю что используется для chunks базы знаний.

# Закрытый словарь
{_build_vocabulary_block()}

# Правила
1. Для каждой оси верни 0-3 значения из словаря.
2. Если ось не релевантна вопросу — пустой массив [].
3. Будь **умеренно строгим**: лучше пропустить ось, чем выбрать сомнительный тег.
   Лишние теги сужают пул retrieval'а и могут отрезать правильный ответ.
4. **Не выдумывай** теги вне словаря.
5. Возвращай ТОЛЬКО JSON-объект, без пояснений и markdown.

# Формат
{{"energy": [...], "market_aspect": [...], "geopolitics": [...], "finance": [...], "region": [...]}}
""".strip()


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(raw: str) -> dict[str, list[str]]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    m = _JSON_RE.search(raw)
    if not m:
        raise ValueError(f"No JSON in classifier response: {raw[:200]!r}")
    return json.loads(m.group(0))


def _validate(parsed: dict[str, Any]) -> TopicTags:
    return TopicTags(
        energy=filter_valid("energy", parsed.get("energy", []) or []),
        market_aspect=filter_valid("market_aspect", parsed.get("market_aspect", []) or []),
        geopolitics=filter_valid("geopolitics", parsed.get("geopolitics", []) or []),
        finance=filter_valid("finance", parsed.get("finance", []) or []),
        region=filter_valid("region", parsed.get("region", []) or []),
    )


class QueryClassifier:
    """Singleton wrapper над kimi-k2p6 для query → TopicTags."""

    _instance: "QueryClassifier | None" = None
    _lock = threading.Lock()

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model_name = model
        self.llm = get_chat_model(provider="hydra", model=model, temperature=0)

    @classmethod
    def get(cls, model: str = DEFAULT_MODEL) -> "QueryClassifier":
        with cls._lock:
            if cls._instance is None or cls._instance.model_name != model:
                cls._instance = cls(model)
            return cls._instance

    async def classify_async(self, query: str) -> TopicTags:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Вопрос: {query}"},
        ]
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                resp = await self.llm.ainvoke(messages)
                content = resp.content if hasattr(resp, "content") else str(resp)
                parsed = _parse_response(content)
                return _validate(parsed)
            except Exception as e:  # noqa: BLE001
                if attempt < RETRY_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    logger.warning("classify failed for %r after %d attempts: %s", query[:80], RETRY_ATTEMPTS, e)
        return TopicTags()

    def classify(self, query: str) -> TopicTags:
        """Sync wrapper — для тестов и CLI."""
        return asyncio.run(self.classify_async(query))

    async def classify_doc_types_async(self, query: str) -> list[str]:
        """Возвращает 0-3 имени document type, релевантных запросу.

        Используется для Chroma `where={"type": {"$in": [...]}}` фильтра —
        narrow retrieval pool до правильного типа (annual vs financial vs
        market_report etc). Решает Роснефть IFRS vs AR cross-doc miss
        (см. docs/experiments/rag-prefix-experiments.md).
        """
        messages = [
            {"role": "system", "content": DOC_TYPE_PROMPT},
            {"role": "user", "content": f"Вопрос: {query}"},
        ]
        valid = set(DOC_TYPE_DESCRIPTIONS.keys())
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                resp = await self.llm.ainvoke(messages)
                content = resp.content if hasattr(resp, "content") else str(resp)
                parsed = _parse_response(content)
                types = parsed.get("types", []) or []
                # Валидация
                clean = [t for t in types if t in valid]
                return clean[:3]
            except Exception as e:  # noqa: BLE001
                if attempt < RETRY_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    logger.warning("classify_doc_types failed for %r: %s", query[:80], e)
        return []

    def classify_doc_types(self, query: str) -> list[str]:
        return asyncio.run(self.classify_doc_types_async(query))


def topic_overlap_score(query_tags: TopicTags, chunk_meta: dict) -> int:
    """Сколько topic-тегов из query совпадают с тегами chunk'а.

    chunk_meta — flat dict из chroma_metadata(), где topic_<axis> хранится
    как comma-separated string. Возвращает суммарное количество matches
    по 5 осям (max теоретически 5*3=15, но обычно 0-5).
    """
    score = 0
    for axis_name in ("energy", "market_aspect", "geopolitics", "finance", "region"):
        q_tags = set(getattr(query_tags, axis_name, []))
        if not q_tags:
            continue
        chunk_axis_str = chunk_meta.get(f"topic_{axis_name}", "") or ""
        c_tags = {t.strip() for t in chunk_axis_str.split(",") if t.strip()}
        score += len(q_tags & c_tags)
    return score
