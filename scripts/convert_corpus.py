#!/usr/bin/env python3
"""Конвертирует корпус PDF в Markdown через Marker.

Идемпотентно: пропускает MD, которые уже на месте, если не передан --force.
Читает список документов из `data/metadata/manifest.yml`, выходные .md
кладёт в `data/markdown/<source_id>.md`.

Usage:
    python scripts/convert_corpus.py                 # все доступные PDF
    python scripts/convert_corpus.py --only opec     # фильтр по подстроке source_id
    python scripts/convert_corpus.py --only opec_asb,bruegel,gov_rf
    python scripts/convert_corpus.py --force         # перезаписать существующие MD
    python scripts/convert_corpus.py --check         # только сверить наличие, не конвертировать

Зависимости — `pip install -r requirements-conversion.txt` (+ torch под backend),
см. docs/adr/0010-pdf-to-markdown-marker.md.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nefteboros.rag.convert import convert_pdf, get_default_converter  # noqa: E402

MANIFEST_PATH = ROOT / "data" / "metadata" / "manifest.yml"
CORPUS_DIR = ROOT / "data" / "corpus"
MARKDOWN_DIR = ROOT / "data" / "markdown"


def _matches_only(doc_id: str, only: list[str] | None) -> bool:
    return not only or any(sub in doc_id for sub in only)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--only",
        help="Запятая-разделённый список подстрок source_id для фильтра",
        default=None,
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Перезаписать MD, даже если файл уже есть",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Только сверить наличие PDF/MD, не запускать конвертацию",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("convert_corpus")

    only = [s.strip() for s in args.only.split(",")] if args.only else None

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    documents = manifest.get("documents", [])

    todo = []
    skipped_present = []
    skipped_no_pdf = []
    for doc in documents:
        sid = doc["id"]
        if not _matches_only(sid, only):
            continue
        file_name = doc.get("file")
        if not file_name:
            continue
        pdf_path = CORPUS_DIR / file_name
        md_path = MARKDOWN_DIR / f"{sid}.md"
        if not pdf_path.exists():
            skipped_no_pdf.append((sid, pdf_path))
            continue
        if md_path.exists() and not args.force:
            skipped_present.append((sid, md_path))
            continue
        todo.append((sid, pdf_path, md_path))

    log.info(
        "Задача: %d документов; skip(MD есть)=%d; skip(нет PDF)=%d",
        len(todo),
        len(skipped_present),
        len(skipped_no_pdf),
    )
    for sid, _ in skipped_present:
        log.info("  [skip MD existing] %s", sid)
    for sid, p in skipped_no_pdf:
        log.warning("  [skip no PDF]      %s (нет %s — запусти fetch_corpus.py)", sid, p)

    if args.check:
        log.info(
            "--check: пропускаем конвертацию. Готово к запуску: %d. Уже есть: %d.",
            len(todo),
            len(skipped_present),
        )
        return 0

    if not todo:
        log.info("Нечего делать.")
        return 0

    log.info("Загружаю Marker (3-5 ГБ моделей, может занять минуту)...")
    t_load = time.monotonic()
    converter = get_default_converter()
    log.info("Marker готов за %.1f сек.", time.monotonic() - t_load)

    total_t0 = time.monotonic()
    ok, failed = 0, 0
    for sid, pdf_path, md_path in todo:
        try:
            r = convert_pdf(pdf_path, md_path, converter=converter, source_id=sid)
            log.info(
                "  [ok] %s — %d стр, %.1f сек, MD %d байт",
                sid,
                r.pages,
                r.duration_sec,
                r.md_bytes,
            )
            ok += 1
        except Exception:
            log.exception("  [fail] %s", sid)
            failed += 1

    log.info(
        "Готово: ok=%d, failed=%d за %.1f сек.",
        ok,
        failed,
        time.monotonic() - total_t0,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
