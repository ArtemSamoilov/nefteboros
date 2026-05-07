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

    def text_for_embedding(self, *, with_heading_prefix: bool = False) -> str:
        """Текст, передаваемый эмбеддеру.

        with_heading_prefix=True добавляет обогащённый prefix вида:
            [{source_title}]
            {clean_section_path}
            >>> {first_meaningful_content_line}

            {text}

        Финальная стратегия (после анализа failure cases в
        docs/experiments/, итерация v3):
        1. source_title в quадратных скобках — даёт embedder'у явный
           identifier документа
        2. section_path очищен от HTML-тегов Marker'а (<span id=...>)
           и от дубликата source_title в начале
        3. first_meaningful_line — реальная под-section, которую chunker
           часто пропускает (bold/CAPS строки не распарсены как H2)

        Без prefix embedding опирается только на raw content — что для
        table-only и similar-chunk корпоративных AR даёт SAME_DOC_MISS.
        """
        if not with_heading_prefix:
            return self.text
        # Чистим section_path
        sp = _clean_heading(self.section_path or "")
        # Если section_path начинается с source_title — отрезаем дубликат
        if sp and self.source_title:
            stitle_clean = _clean_heading(self.source_title)
            for sep in (" > ", " — ", " - "):
                prefix_to_strip = f"{stitle_clean}{sep}"
                if sp.startswith(prefix_to_strip):
                    sp = sp[len(prefix_to_strip):]
                    break
        sp = sp or "(no section)"

        first_line = _first_meaningful_line(self.text)

        parts = [f"[{self.source_title}]", sp]
        if first_line:
            parts.append(f">>> {first_line}")
        prefix = "\n".join(parts)
        return f"{prefix}\n\n{self.text}"

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
