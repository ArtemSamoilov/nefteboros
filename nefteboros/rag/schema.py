"""Pydantic-схемы для RAG-pipeline.

См. ADR-0011 (chunking) и ADR-0012 (embed + retrieve, future).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Block = Literal["1_strategy", "2_corporate", "3_operational", "4_geopolitics"]
Language = Literal["ru", "en"]


class TopicTags(BaseModel):
    """Topic-tags по 5 осям закрытого словаря (см. topic_vocabulary.py)."""

    energy: list[str] = Field(default_factory=list)
    market_aspect: list[str] = Field(default_factory=list)
    geopolitics: list[str] = Field(default_factory=list)
    finance: list[str] = Field(default_factory=list)
    region: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    """Чанк после PR B (chunker + tagger). Готов к эмбеддингу в PR C."""

    # Идентификация
    id: str = Field(..., description="Globally unique: f'{source_id}__{idx:04d}'")
    source_id: str
    chunk_idx: int
    text: str
    token_count: int

    # Source-tags (из manifest.yml, детерминированно)
    source_title: str
    publisher: str
    block: Block
    type: str
    language: Language
    date: str = Field(..., description="ISO date or year-only string")

    # Section-tags (из MD-структуры)
    headings: list[str] = Field(
        default_factory=list,
        description="Heading hierarchy от H1 до самого глубокого, например ['3. Production Outlook', '3.2 OPEC Production']",
    )
    section_path: str = Field(
        default="",
        description="Heading hierarchy concatenated through ' > '",
    )
    page_start: int | None = None
    page_end: int | None = None
    has_table: bool = False
    is_table_only: bool = Field(
        default=False,
        description="True если чанк целиком — одна большая таблица (выделена по правилу > max_tokens)",
    )

    # Topic-tags (от LLM, см. topic_vocabulary.py)
    topic: TopicTags = Field(default_factory=TopicTags)

    def text_for_embedding(self, *, with_heading_prefix: bool = False) -> str:
        """Текст, передаваемый эмбеддеру.

        with_heading_prefix=True добавляет prefix вида:
            [{source_title}]
            {section_path}

            {text}

        Это даёт BGE-M3 контекст «откуда» чанк (см. эксперимент в
        docs/experiments/rag-baseline-v2-heading-prefix.md). Без prefix
        embedding опирается только на raw content — что для table-only
        и similar-chunk корпоративных AR даёт SAME_DOC_MISS.
        """
        if not with_heading_prefix:
            return self.text
        sp = self.section_path or "(no section)"
        return f"[{self.source_title}]\n{sp}\n\n{self.text}"

    def chroma_metadata(self) -> dict:
        """Сериализация в плоский dict для Chroma metadata.

        Chroma не принимает вложенные dict / list — конвертируем topic-tags в comma-separated.
        """
        return {
            "source_id": self.source_id,
            "chunk_idx": self.chunk_idx,
            "source_title": self.source_title,
            "publisher": self.publisher,
            "block": self.block,
            "type": self.type,
            "language": self.language,
            "date": self.date,
            "section_path": self.section_path,
            "page_start": self.page_start if self.page_start is not None else -1,
            "page_end": self.page_end if self.page_end is not None else -1,
            "has_table": self.has_table,
            "is_table_only": self.is_table_only,
            # Topic-tags — comma-separated для Chroma (где str only)
            "topic_energy": ",".join(self.topic.energy),
            "topic_market_aspect": ",".join(self.topic.market_aspect),
            "topic_geopolitics": ",".join(self.topic.geopolitics),
            "topic_finance": ",".join(self.topic.finance),
            "topic_region": ",".join(self.topic.region),
        }
