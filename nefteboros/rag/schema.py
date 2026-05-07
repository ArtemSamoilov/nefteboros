"""Pydantic-схемы для RAG-pipeline.

См. ADR-0011 (chunking) и ADR-0016 (embed + retrieve).
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

# Marker иногда оставляет HTML <span id="..."> в headings — выкидываем
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Множественные пробелы → одиночный
_WS_RE = re.compile(r"\s+")
# Markdown bold/italic markers вокруг строк
_MD_FORMAT_RE = re.compile(r"^[*_#>\-\s]+|[*_\s]+$")


def _clean_heading(text: str) -> str:
    """Убирает HTML-теги Marker'а и нормализует пробелы."""
    text = _HTML_TAG_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _first_meaningful_line(text: str, *, max_chars: int = 120) -> str:
    """Первая «значимая» строка content — часто это bold/CAPS заголовок,
    который Marker не parsed как H2/H3. Помогает дать embedding контекст
    реального content sub-section, когда chunker присвоил неточный
    section_path (см. failure analysis в docs/experiments/).
    """
    for raw in text.split("\n"):
        line = _MD_FORMAT_RE.sub("", raw).strip()
        if not line or line.startswith(("|", "{")):
            # таблицы / page-маркеры пропускаем
            continue
        if len(line) < 10:
            # слишком короткое (часто артефакты типа "Page 47", "Table 3")
            continue
        return line[:max_chars]
    return ""

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

    def text_for_embedding(self, *, with_heading_prefix: bool = True) -> str:
        """Текст, передаваемый эмбеддеру.

        with_heading_prefix=True (default) — простой prefix:
            [{source_title}]
            {section_path}

            {text}

        Это финальная конфигурация после 4 экспериментов на 95-Q датасете
        (см. docs/experiments/rag-prefix-experiments.md):
          v1 baseline (no prefix): chunk_hit@5 = 0.653
          v2 simple prefix:        chunk_hit@5 = 0.779 ⭐
          v3 + first_line:         chunk_hit@5 = 0.768 (регресс text_only/table_only)
          v4 + HTML clean + dedup: chunk_hit@5 = 0.758 (регресс — чистка ослабила сигнал)

        Решение: **v2 (simple) — production default**. Чистка HTML-тегов и
        dedup source_title сделали хуже, видимо `<span id="page-N">` несли
        полезный page-сигнал, а двойное упоминание source_title усиливало
        identity. Меньше manipulations — лучше для BGE-M3.

        Утилиты `_clean_heading()` и `_first_meaningful_line()` оставлены
        в модуле для возможных будущих экспериментов (например, отдельная
        конфигурация для table-only chunks).
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
