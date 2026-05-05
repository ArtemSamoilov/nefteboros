#!/usr/bin/env python3
"""Fetch nefteboros RAG corpus by manifest.

Идемпотентно: пропускает файлы, уже присутствующие с правильным sha256.
Skip-условия:
  - status: manual_required  (требует ручной регистрации на источнике)
  - status: pending_manual   (требует VPN РФ — см. fetch_corpus_manual.sh)

Usage:
    python scripts/fetch_corpus.py            # тянет всё доступное
    python scripts/fetch_corpus.py --only iea # фильтр по подстроке id
    python scripts/fetch_corpus.py --force    # перекачать всё
    python scripts/fetch_corpus.py --check    # только сверить sha256, не качать
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "data" / "metadata" / "manifest.yml"
CORPUS_DIR = ROOT / "data" / "corpus"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)
TIMEOUT_SEC = 240


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_url(url: str, dest: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:
        data = r.read()
    dest.write_bytes(data)
    return len(data)


def process_doc(doc: dict, *, force: bool, check_only: bool) -> str:
    """Return one of: ok, skip, fail, manual."""
    doc_id = doc["id"]
    file_name = doc.get("file")
    status = doc.get("status")

    if status in {"manual_required", "pending_manual"}:
        print(f"[manual] {doc_id} — {status} ({doc.get('note', 'see manifest')})")
        return "manual"

    if not file_name:
        print(f"[fail]   {doc_id} — no `file` field in manifest")
        return "fail"

    out = CORPUS_DIR / file_name
    expected_sha = doc.get("sha256")

    if out.exists() and not force:
        actual = sha256_file(out)
        if expected_sha and actual != expected_sha:
            print(
                f"[WARN]   {doc_id} — sha mismatch:\n"
                f"           actual:   {actual}\n"
                f"           expected: {expected_sha}"
            )
            return "fail"
        size = out.stat().st_size
        print(f"[skip]   {doc_id} ({size:,} bytes, sha ok)")
        return "skip"

    if check_only:
        print(f"[miss]   {doc_id} — file not present, would fetch")
        return "fail"

    urls = [doc["url"], *doc.get("url_alts", [])]
    for url in urls:
        try:
            size = fetch_url(url, out)
        except (urllib.error.URLError, OSError) as e:
            print(f"[retry]  {doc_id} <- {url} : {e}")
            continue

        actual = sha256_file(out)
        if expected_sha and actual != expected_sha:
            print(
                f"[WARN]   {doc_id} downloaded but sha differs from manifest:\n"
                f"           actual:   {actual}\n"
                f"           expected: {expected_sha}"
            )
            # сохраняем файл но возвращаем fail — нужно либо обновить manifest, либо проверить
            return "fail"
        print(f"[ok]     {doc_id} ({size:,} bytes) <- {url}")
        return "ok"

    if out.exists():
        out.unlink()
    print(f"[fail]   {doc_id} — все URL не сработали")
    return "fail"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", help="Подстрока для фильтра по id документа")
    p.add_argument("--force", action="store_true", help="Перекачать даже если файл уже есть")
    p.add_argument("--check", action="store_true", help="Только сверить sha256, не качать")
    args = p.parse_args()

    if not MANIFEST_PATH.exists():
        print(f"manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        return 2

    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    documents = manifest.get("documents", [])
    if args.only:
        documents = [d for d in documents if args.only in d["id"]]
        if not documents:
            print(f"No documents match --only {args.only!r}", file=sys.stderr)
            return 2

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    counts = {"ok": 0, "skip": 0, "fail": 0, "manual": 0}
    for doc in documents:
        result = process_doc(doc, force=args.force, check_only=args.check)
        counts[result] += 1

    print(
        f"\nResult: ok={counts['ok']}  skip={counts['skip']}  "
        f"fail={counts['fail']}  manual={counts['manual']}"
    )
    return 0 if counts["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
