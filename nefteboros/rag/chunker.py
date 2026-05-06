"""Markdown → list[Chunk] (heading-aware splitter).

Парсит MD из Marker (см. ADR-0010), режет по структуре заголовков с таргет-размером
~3000 токенов / max 4000 (см. ADR-0011 — большие чанки под Kimi 2.6). Таблицы —
не режем без необходимости; страничные маркеры `{N}` сохраняются в metadata.

Tagging (source/section заполняется здесь, topic — в `tagger.py`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .schema import Block, Chunk, Language, TopicTags

# -----------------------------------------------------------------------------
# Regex
# -----------------------------------------------------------------------------

# Page marker: строка `{N}-----` или просто `{N}` (Marker paginate_output=True)
PAGE_MARKER_RE = re.compile(r"^\{(\d+)\}[\-]*\s*$", re.MULTILINE)

# Picture references — визуальный шум для embedding
PICTURE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")

# Heading line: `## **Title**`, `### Title`, etc. Поддерживаем bold внутри.
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# Table-row: starts with `|`, ends with `|`
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")

# Bold/italic markers внутри title — сводим к чистому тексту
INLINE_FORMAT_RE = re.compile(r"\*+([^*]+)\*+")


# -----------------------------------------------------------------------------
# Token estimation
# -----------------------------------------------------------------------------

CHARS_PER_TOKEN = 3.5  # эмпирическая оценка для смешанного RU+EN корпуса


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


# -----------------------------------------------------------------------------
# Структуры
# -----------------------------------------------------------------------------


@dataclass
class MdBlock:
    """Семантический блок MD: либо `heading`, либо `content` (paragraphs/tables)."""

    text: str
    is_heading: bool = False
    heading_level: int = 0
    is_table: bool = False
    page_start: int | None = None
    page_end: int | None = None

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class ChunkSpec:
    """Промежуточная структура: набор блоков, готовый стать Chunk."""

    blocks: list[MdBlock] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)  # heading-path (H1, H2, ..., самый глубокий)

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks).strip()

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    @property
    def page_start(self) -> int | None:
        for b in self.blocks:
            if b.page_start is not None:
                return b.page_start
        return None

    @property
    def page_end(self) -> int | None:
        for b in reversed(self.blocks):
            if b.page_end is not None:
                return b.page_end
        return None

    @property
    def has_table(self) -> bool:
        return any(b.is_table for b in self.blocks)

    @property
    def is_table_only(self) -> bool:
        return bool(self.blocks) and all(b.is_table or b.is_heading for b in self.blocks)


# -----------------------------------------------------------------------------
# Парсинг MD
# -----------------------------------------------------------------------------


def _strip_inline_format(s: str) -> str:
    """`**Title**` → `Title`, `*Italic*` → `Italic`."""
    return INLINE_FORMAT_RE.sub(r"\1", s).strip()


def _split_into_lines_with_pages(text: str) -> tuple[list[str], dict[int, int]]:
    """Возвращает (lines, line_idx_to_page_after_this_line).

    Page marker `{N}---` стрипается из вывода и регистрируется как «после строки N
    идёт страница X».
    """
    lines: list[str] = []
    page_after: dict[int, int] = {}
    current_page: int | None = None

    for raw_line in text.splitlines():
        m = PAGE_MARKER_RE.match(raw_line)
        if m:
            current_page = int(m.group(1))
            # Маркер не сохраняем в lines — он «между» строк
            page_after[len(lines) - 1] = current_page
            continue
        lines.append(raw_line)

    return lines, page_after


def _line_to_block(
    buffer: list[str],
    in_table: bool,
    page_start: int | None,
    page_end: int | None,
) -> MdBlock | None:
    if not buffer:
        return None
    text = "\n".join(buffer).strip()
    if not text:
        return None
    return MdBlock(
        text=text,
        is_table=in_table,
        page_start=page_start,
        page_end=page_end,
    )


def parse_markdown(text: str) -> list[MdBlock]:
    """Парсит MD в плоский список MdBlock (heading-блоки и content-блоки).

    Стрипает picture references. Сохраняет page-границы (page_start/page_end на блок).
    """
    text = PICTURE_RE.sub("", text)  # шум удаляем сразу
    lines, page_after = _split_into_lines_with_pages(text)

    blocks: list[MdBlock] = []
    buffer: list[str] = []
    in_table = False
    last_page: int | None = page_after.get(-1)
    buffer_page_start: int | None = last_page

    def flush():
        nonlocal buffer, buffer_page_start
        if buffer:
            block = _line_to_block(buffer, in_table, buffer_page_start, last_page)
            if block is not None:
                blocks.append(block)
            buffer = []
            buffer_page_start = last_page

    for idx, line in enumerate(lines):
        # Регистрируем page переход «после этой строки» (он фиксируется уже после flush)
        is_blank = not line.strip()
        is_table_line = bool(TABLE_LINE_RE.match(line))
        m_head = HEADING_RE.match(line) if not is_table_line else None

        if m_head:
            flush()
            heading_text = _strip_inline_format(m_head.group(2))
            blocks.append(
                MdBlock(
                    text=line.strip(),
                    is_heading=True,
                    heading_level=len(m_head.group(1)),
                    page_start=last_page,
                    page_end=last_page,
                )
            )
            in_table = False
        elif is_table_line:
            if not in_table:
                flush()
                in_table = True
            buffer.append(line)
        elif is_blank:
            if buffer:
                flush()
                in_table = False
        else:
            if in_table:
                # таблица закончилась
                flush()
                in_table = False
            buffer.append(line)

        # Если после этой строки был page-маркер — обновляем last_page
        if idx in page_after:
            last_page = page_after[idx]

    flush()
    # Подставим heading-text вместо сырых '##' для удобства downstream:
    for b in blocks:
        if b.is_heading:
            stripped = HEADING_RE.match(b.text)
            if stripped:
                b.text = _strip_inline_format(stripped.group(2))
    return blocks


# -----------------------------------------------------------------------------
# Splitter: blocks → ChunkSpecs
# -----------------------------------------------------------------------------


def _path_with(stack: list[tuple[int, str]]) -> list[str]:
    return [text for _level, text in stack]


def _flatten_table_to_lines(text: str) -> list[str]:
    """Marker artifact: широкие таблицы (EI Stat Review) приходят как ячейки
    через `<br>`. Чтобы получить разумный размер чанка — разворачиваем
    `<br>` в строки, выбрасываем pipe-separators, чистим whitespace.
    Результат — список текстовых «строк-ячеек», пригодных для плоского
    chunking. Тут сознательно теряем визуальную структуру таблицы ради
    того, чтобы каждая часть влезала в embedding-окно.
    """
    flat = text.replace("<br>", "\n").replace("|", " ")
    return [ln.strip() for ln in flat.split("\n") if ln.strip()]


def _pack_lines_into_blocks(
    lines: list[str],
    *,
    max_tokens: int,
    page_start: int | None,
    page_end: int | None,
) -> list[MdBlock]:
    """Жадная упаковка плоских строк в табличные блоки ≤ max_tokens."""
    fragments: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0
    for ln in lines:
        ln_tokens = estimate_tokens(ln)
        if current and current_tokens + ln_tokens > max_tokens:
            fragments.append(current)
            current = []
            current_tokens = 0
        current.append(ln)
        current_tokens += ln_tokens
    if current:
        fragments.append(current)
    return [
        MdBlock(
            text="\n".join(frag),
            is_table=True,
            page_start=page_start,
            page_end=page_end,
        )
        for frag in fragments
    ]


def _split_table_block(table: MdBlock, max_tokens: int) -> list[MdBlock]:
    """Большая таблица → несколько кусков с продублированным header.

    Markdown-таблица имеет header (строка 1) + separator (строка 2: `|---|---|`)
    + data rows. Дублируем первые две строки в каждый фрагмент.

    Edge case 1: «giant single-row» (lines<3) — Marker свернул таблицу в
    ячейки через `<br>`. Разворачиваем в плоский текст и режем как plain.

    Edge case 2: широкая таблица с большим количеством rows, где каждая
    отдельная row > max_tokens (тоже из-за `<br>` внутри ячеек). Не пытаемся
    сохранить таблицу как таковую — переходим в плоский режим как case 1.
    """
    text = table.text
    lines = text.split("\n")

    # Case 1: ≤2 строк → giant single-row, идём в плоский режим
    # Case 2: есть row, которая сама по себе > max_tokens → плоский режим целиком
    # Case 3: header (первые 2 строки) занимает >40% max_tokens — даже header+1 row
    #         с дублированием в каждый фрагмент даст overflow → плоский режим
    # Case 4: общий объём таблицы > 4× max_tokens с большим количеством <br>-cell-pack —
    #         симптом «широкой» таблицы с многострочными ячейками, плоский режим
    needs_flat = (
        len(lines) < 3
        or any(estimate_tokens(ln) > max_tokens for ln in lines[2:])
        or estimate_tokens("\n".join(lines[:2])) > max_tokens * 0.4
        or (table.tokens > max_tokens * 4 and text.count("<br>") > 50)
    )
    if needs_flat:
        flat_lines = _flatten_table_to_lines(text)
        if not flat_lines:
            return [table]
        return _pack_lines_into_blocks(
            flat_lines,
            max_tokens=max_tokens,
            page_start=table.page_start,
            page_end=table.page_end,
        )

    header = lines[:2]  # title row + separator
    data = lines[2:]

    header_tokens = estimate_tokens("\n".join(header))
    fragments: list[list[str]] = []
    current: list[str] = []
    current_tokens = header_tokens
    for row in data:
        row_tokens = estimate_tokens(row)
        if current and current_tokens + row_tokens > max_tokens:
            fragments.append(current)
            current = []
            current_tokens = header_tokens
        current.append(row)
        current_tokens += row_tokens
    if current:
        fragments.append(current)

    return [
        MdBlock(
            text="\n".join(header + frag),
            is_table=True,
            page_start=table.page_start,
            page_end=table.page_end,
        )
        for frag in fragments
    ]


def split_into_chunkspecs(
    blocks: list[MdBlock],
    *,
    target_tokens: int = 3000,
    max_tokens: int = 4000,
    min_tokens: int = 200,
) -> list[ChunkSpec]:
    """Heading-aware splitter с табличной спецлогикой.

    Алгоритм:
      1. Идём по блокам, поддерживаем стек активных headings (H1, H2, ...).
      2. Накопляем блоки в текущий ChunkSpec.
      3. Закрываем chunk при условии:
         - размер ≥ target_tokens И встретили H2/H3 boundary, или
         - размер + new block > max_tokens.
      4. Большая таблица (> max) — режется на фрагменты по rows с
         продублированным header в каждом, каждый фрагмент → свой chunk
         is_table_only=True.
      5. После сборки — кванты < min_tokens мерджатся с предыдущим (или, если
         первый, с следующим), чтобы не плодить шум-чанков по 30-40 токенов.
    """
    chunks: list[ChunkSpec] = []
    stack: list[tuple[int, str]] = []  # heading-path (level, title)
    current = ChunkSpec()

    def push_chunk():
        nonlocal current
        if current.blocks:
            current.headings = _path_with(stack)
            chunks.append(current)
        current = ChunkSpec()

    for block in blocks:
        if block.is_heading:
            # Открываем новую section: закрываем текущий chunk если уже накопил target
            if current.tokens >= target_tokens:
                push_chunk()
            # Обновляем стек headings (выкидываем equal-or-deeper)
            while stack and stack[-1][0] >= block.heading_level:
                stack.pop()
            stack.append((block.heading_level, block.text))
            current.blocks.append(block)
            continue

        # Большая таблица — bursting на фрагменты
        if block.is_table and block.tokens > max_tokens:
            push_chunk()
            for frag in _split_table_block(block, max_tokens):
                spec = ChunkSpec(blocks=[frag], headings=_path_with(stack))
                chunks.append(spec)
            continue

        projected = current.tokens + block.tokens
        if projected > max_tokens and current.blocks:
            push_chunk()
            current.blocks.append(block)
        else:
            current.blocks.append(block)

    push_chunk()
    # Удаляем «пустые» chunks (только heading)
    chunks = [c for c in chunks if any(not b.is_heading for b in c.blocks)]
    # Мерджим too-small с соседним
    chunks = _merge_tiny(chunks, min_tokens=min_tokens, max_tokens=max_tokens)
    return chunks


def _merge_tiny(
    chunks: list[ChunkSpec], *, min_tokens: int, max_tokens: int
) -> list[ChunkSpec]:
    """Двухпроходное склеивание короткиx (< min_tokens) чанков с соседями.

    1. Forward pass: tiny → prev (если prev не is_table_only и влезает в max).
    2. Backward pass: оставшиеся tiny → next (если next не is_table_only и влезает).
    3. is_table_only-чанки (даже если короткие — например, маленькая отдельная таблица)
       не сливаем — таблицы хранятся атомарно.

    Альтернатива «отбрасывать tiny» — отвергнута: они часто содержат коротенькие
    выводы под таблицей, которые ценны для retrieval.
    """
    if len(chunks) <= 1:
        return chunks

    # forward: tiny → prev
    pass1: list[ChunkSpec] = [chunks[0]]
    for spec in chunks[1:]:
        prev = pass1[-1]
        if (
            spec.tokens < min_tokens
            and not spec.is_table_only
            and not prev.is_table_only
            and prev.tokens + spec.tokens <= max_tokens
        ):
            prev.blocks.extend(spec.blocks)
        else:
            pass1.append(spec)

    # backward: tiny → next
    if len(pass1) <= 1:
        return pass1
    pass2: list[ChunkSpec] = [pass1[-1]]
    for spec in reversed(pass1[:-1]):
        nxt = pass2[-1]
        if (
            spec.tokens < min_tokens
            and not spec.is_table_only
            and not nxt.is_table_only
            and spec.tokens + nxt.tokens <= max_tokens
        ):
            # вставляем blocks `spec` в начало `nxt`
            nxt.blocks = list(spec.blocks) + list(nxt.blocks)
        else:
            pass2.append(spec)
    return list(reversed(pass2))


# -----------------------------------------------------------------------------
# Сборка финальных Chunk объектов с source-tags
# -----------------------------------------------------------------------------


def assemble_chunks(
    specs: list[ChunkSpec],
    *,
    source_id: str,
    source_title: str,
    publisher: str,
    block: Block,
    type_: str,
    language: Language,
    date: str,
) -> list[Chunk]:
    out: list[Chunk] = []
    for idx, spec in enumerate(specs):
        out.append(
            Chunk(
                id=f"{source_id}__{idx:04d}",
                source_id=source_id,
                chunk_idx=idx,
                text=spec.text,
                token_count=spec.tokens,
                source_title=source_title,
                publisher=publisher,
                block=block,
                type=type_,
                language=language,
                date=date,
                headings=spec.headings,
                section_path=" > ".join(spec.headings),
                page_start=spec.page_start,
                page_end=spec.page_end,
                has_table=spec.has_table,
                is_table_only=spec.is_table_only,
                topic=TopicTags(),  # заполняется в tagger.py (PR B продолжение)
            )
        )
    return out


# -----------------------------------------------------------------------------
# Convenience: file → chunks (без tagging)
# -----------------------------------------------------------------------------


def chunk_md_file(
    md_path: Path,
    *,
    source_id: str,
    source_title: str,
    publisher: str,
    block: Block,
    type_: str,
    language: Language,
    date: str,
    target_tokens: int = 3000,
    max_tokens: int = 4000,
) -> list[Chunk]:
    text = md_path.read_text(encoding="utf-8")
    blocks = parse_markdown(text)
    specs = split_into_chunkspecs(blocks, target_tokens=target_tokens, max_tokens=max_tokens)
    return assemble_chunks(
        specs,
        source_id=source_id,
        source_title=source_title,
        publisher=publisher,
        block=block,
        type_=type_,
        language=language,
        date=date,
    )


def iter_chunks_for_corpus(
    md_dir: Path,
    manifest_documents: Iterable[dict],
    *,
    target_tokens: int = 3000,
    max_tokens: int = 4000,
) -> Iterable[Chunk]:
    """Итератор по чанкам всего корпуса: matched documents из manifest + наличие .md."""
    for doc in manifest_documents:
        sid = doc["id"]
        md_path = md_dir / f"{sid}.md"
        if not md_path.exists():
            continue
        yield from chunk_md_file(
            md_path,
            source_id=sid,
            source_title=doc.get("title", sid),
            publisher=doc.get("publisher", ""),
            block=doc.get("block"),
            type_=doc.get("type", ""),
            language=doc.get("language", "en"),
            date=str(doc.get("date", "")),
            target_tokens=target_tokens,
            max_tokens=max_tokens,
        )
