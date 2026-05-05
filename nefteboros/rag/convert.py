"""PDF → Markdown через Marker.

Этап 1 RAG-pipeline: layout-aware конвертация PDF в структурированный markdown
с сохранением таблиц и страничных маркеров. Выход — `data/markdown/<source_id>.md`.

Marker (`marker-pdf`) — отдельная зависимость, см. requirements-conversion.txt
и docs/adr/0010-pdf-to-markdown-marker.md.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MARKER_CONFIG: dict[str, Any] = {
    "output_format": "markdown",
    "paginate_output": True,  # вставит {N} разделители страниц для будущего chunking
    "force_ocr": False,  # OCR только при необходимости (большинство наших PDF — native)
}


@dataclass
class ConversionResult:
    """Результат одного PDF→MD прохода."""

    source_id: str
    pdf_path: Path
    md_path: Path
    pages: int
    duration_sec: float
    md_bytes: int


def get_default_converter():
    """Lazy-load Marker конвертер.

    Marker подгружает 3-5 ГБ ML-моделей (Surya OCR + table-structure + layout)
    при первом обращении, поэтому импортируем внутри функции.
    Возвращённый объект безопасно переиспользовать для batch-конвертации.
    """
    from marker.config.parser import ConfigParser
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    config_parser = ConfigParser(MARKER_CONFIG)
    return PdfConverter(
        artifact_dict=create_model_dict(),
        config=config_parser.generate_config_dict(),
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
    )


def convert_pdf(
    pdf_path: Path,
    md_path: Path,
    *,
    converter=None,
    source_id: str | None = None,
) -> ConversionResult:
    """Конвертирует один PDF в Markdown и сохраняет на диск.

    Args:
        pdf_path: входной PDF.
        md_path: куда положить .md.
        converter: pre-loaded Marker конвертер (для batch). None → загрузим внутри.
        source_id: для логов; default — stem md_path.

    Returns:
        ConversionResult со временем, количеством страниц и размером MD.
    """
    if converter is None:
        converter = get_default_converter()

    sid = source_id or md_path.stem
    logger.info("convert: %s -> %s", pdf_path.name, md_path.name)

    t0 = time.monotonic()
    rendered = converter(str(pdf_path))
    duration = time.monotonic() - t0

    # Marker ≥1.5: rendered.markdown (str), rendered.metadata (dict с page_count)
    md_text = rendered.markdown
    page_count = rendered.metadata.get("page_count", 0) if hasattr(rendered, "metadata") else 0

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    return ConversionResult(
        source_id=sid,
        pdf_path=pdf_path,
        md_path=md_path,
        pages=page_count,
        duration_sec=duration,
        md_bytes=len(md_text.encode("utf-8")),
    )
