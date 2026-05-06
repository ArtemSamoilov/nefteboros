"""LLM-based topic tagger для RAG-чанков.

Заполняет TopicTags по закрытому словарю (см. topic_vocabulary.py) через
HydraGPT (kimi-k2p6 по умолчанию) — для каждого чанка отдельный вызов.

Async с семафором для ограничения concurrent requests. Robustен к LLM-ошибкам:
parse failure → пустые tags + log warning.

См. ADR-0011 (chunking + tagging).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from nefteboros.llm import get_chat_model
from nefteboros.rag.schema import Chunk, TopicTags
from nefteboros.rag.topic_vocabulary import (
    TAG_DESCRIPTIONS,
    VOCABULARY,
    filter_valid,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "kimi-k2p6"
DEFAULT_CONCURRENCY = 20
RETRY_ATTEMPTS = 3
RETRY_DELAY_SEC = 2.0


def _build_vocabulary_block() -> str:
    """Формирует читаемый словарь для system-промпта."""
    lines = []
    for axis, values in VOCABULARY.items():
        lines.append(f"\n## {axis}")
        for v in values:
            lines.append(f"- {v}: {TAG_DESCRIPTIONS[v]}")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""Ты — классификатор фрагментов нефтегазовых документов. Получаешь текстовый фрагмент и возвращаешь structured JSON с тегами по 5 осям из закрытого словаря.

# Закрытый словарь
{_build_vocabulary_block()}

# Правила
1. Для каждой оси выбери от 0 до 3 значений из списка выше.
2. Если ось не релевантна фрагменту — оставь пустой массив [].
3. **Запрещено** выдумывать теги вне словаря — будут отброшены при валидации.
4. Возвращай **только JSON-объект**, без пояснений и markdown.

# Формат
{{"energy": [...], "market_aspect": [...], "geopolitics": [...], "finance": [...], "region": [...]}}
""".strip()


USER_PROMPT_TEMPLATE = """Источник: {source_title}
Раздел: {section_path}
Язык: {language}

Фрагмент:
{text}"""


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(raw: str) -> dict[str, list[str]]:
    """Извлекает JSON из ответа LLM. Толерантен к code-block обёртке."""
    raw = raw.strip()
    # ```json … ```
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    match = _JSON_RE.search(raw)
    if not match:
        raise ValueError(f"No JSON object in LLM response: {raw[:200]!r}")
    return json.loads(match.group(0))


def _validate_tags(parsed: dict[str, Any]) -> TopicTags:
    """Применяет filter_valid по каждой оси, отбрасывает мусор."""
    return TopicTags(
        energy=filter_valid("energy", parsed.get("energy", []) or []),
        market_aspect=filter_valid("market_aspect", parsed.get("market_aspect", []) or []),
        geopolitics=filter_valid("geopolitics", parsed.get("geopolitics", []) or []),
        finance=filter_valid("finance", parsed.get("finance", []) or []),
        region=filter_valid("region", parsed.get("region", []) or []),
    )


def _truncate_for_prompt(text: str, max_chars: int = 6000) -> str:
    """Lim текст в промпте — kimi-k2p6 контекст большой, но платим за токены."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 50] + "\n\n[…усечено…]"


async def _tag_one(chunk: Chunk, llm: Any) -> TopicTags:
    """Один чанк → topic-tags. Retries при сетевых сбоях."""
    user = USER_PROMPT_TEMPLATE.format(
        source_title=chunk.source_title,
        section_path=chunk.section_path or "(без раздела)",
        language=chunk.language,
        text=_truncate_for_prompt(chunk.text),
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    last_err: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = await llm.ainvoke(messages)
            content = resp.content if hasattr(resp, "content") else str(resp)
            parsed = _parse_response(content)
            return _validate_tags(parsed)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY_SEC * attempt)
            else:
                logger.warning(
                    "tag_one failed for %s after %d attempts: %s",
                    chunk.id,
                    RETRY_ATTEMPTS,
                    e,
                )
    # все ретраи провалились — возвращаем пустые теги
    _ = last_err  # помечено
    return TopicTags()


async def tag_chunks_async(
    chunks: list[Chunk],
    *,
    model: str = DEFAULT_MODEL,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress_every: int = 25,
) -> list[Chunk]:
    """Тегирует список чанков, изменяет copy с .topic заполненным."""
    llm = get_chat_model(provider="hydra", model=model, temperature=0)
    sem = asyncio.Semaphore(concurrency)
    done = [0]
    total = len(chunks)
    results: list[Chunk] = [None] * total  # type: ignore[list-item]

    async def worker(i: int, c: Chunk):
        async with sem:
            tags = await _tag_one(c, llm)
            tagged = c.model_copy(update={"topic": tags})
            results[i] = tagged
            done[0] += 1
            if done[0] % progress_every == 0 or done[0] == total:
                logger.info("tagged %d/%d chunks", done[0], total)

    await asyncio.gather(*(worker(i, c) for i, c in enumerate(chunks)))
    return results


def tag_chunks(
    chunks: list[Chunk],
    *,
    model: str = DEFAULT_MODEL,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[Chunk]:
    """Sync обёртка над tag_chunks_async для CLI и тестов."""
    return asyncio.run(
        tag_chunks_async(chunks, model=model, concurrency=concurrency)
    )
