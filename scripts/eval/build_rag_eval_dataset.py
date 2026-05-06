#!/usr/bin/env python3
"""Генерация eval-датасета для RAG retriever.

Стратегия (semi-synthetic):
  1. Стратифицированно сэмплируем чанки — N (default 4) на каждый из 25 source_id.
  2. Для каждого чанка через kimi-k2p6 генерируем 1 вопрос на естественном
     языке + краткий ground truth answer.
  3. Сохраняем в `datasets/rag_eval/<version>.jsonl` со схемой:
     {
       "id": "{source_id}__{chunk_idx:04d}__q",
       "question": str,
       "expected_chunk_id": str,
       "expected_source_id": str,
       "source_title": str,
       "section_path": str,
       "language": "ru" | "en",
       "block": "1_strategy" | "2_corporate" | "3_operational" | "4_geopolitics",
       "answer_summary": str  (для quick spot-checking)
     }

Известное ограничение: synthetic вопросы могут быть тривиально близки
к тексту чанка. Это дает «потолок precision сверху» — реальные пользовательские
вопросы будут формулироваться иначе. Но даёт baseline для сравнения
конфигураций (bi-encoder vs +reranker vs +LLM-rerank).

Usage:
    python scripts/eval/build_rag_eval_dataset.py --per-source 4 --version v1
    python scripts/eval/build_rag_eval_dataset.py --per-source 1 --only opec  # subset
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nefteboros.llm import get_chat_model  # noqa: E402
from nefteboros.rag.schema import Chunk  # noqa: E402

CHUNKS_DIR = ROOT / "data" / "chunks"
OUT_DIR = ROOT / "datasets" / "rag_eval"
DEFAULT_MODEL = "kimi-k2p6"
DEFAULT_CONCURRENCY = 12
RANDOM_SEED = 42

def _build_system_prompt(language: str) -> str:
    """language: 'русском' | 'английском' (для подстановки в текст промпта)."""
    return f"""\
Ты — эксперт нефтегазового рынка. На основе данного фрагмента отчёта
сгенерируй ОДИН вопрос, который реалистичный пользователь (топ-менеджер
крупного российского банка) мог бы задать AI-ассистенту-аналитику.

Требования к вопросу:
1. Естественный, без упоминания «по этому фрагменту» / «в данном тексте»
2. Имеет чёткий, проверяемый ответ в этом фрагменте
3. На том же языке, что фрагмент ({language})
4. НЕ содержит точных цитат — пользователь не знает контента отчёта
5. Конкретный — про числа, события, сроки, причины (не «что вы думаете о...»)

Также напиши краткий answer_summary (1-2 предложения) для quick verification.

Возвращай ТОЛЬКО JSON, без markdown-обёртки:
{{"question": "...", "answer_summary": "..."}}"""


USER_PROMPT_TEMPLATE = """\
Источник: {source_title}
Раздел: {section_path}

Фрагмент:
{text}"""


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    m = _JSON_RE.search(raw)
    if not m:
        raise ValueError(f"No JSON in response: {raw[:200]!r}")
    return json.loads(m.group(0))


def _truncate_for_prompt(text: str, max_chars: int = 6000) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 50] + "\n\n[…усечено…]"


def load_chunks_grouped() -> dict[str, list[Chunk]]:
    by_source: dict[str, list[Chunk]] = {}
    for f in sorted(CHUNKS_DIR.glob("*.jsonl")):
        sid = f.stem
        by_source[sid] = []
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                by_source[sid].append(Chunk.model_validate_json(line))
    return by_source


def sample_chunks(
    by_source: dict[str, list[Chunk]],
    *,
    per_source: int,
    only: list[str] | None = None,
    min_tokens: int = 500,
) -> list[Chunk]:
    """Стратифицированный sampling: ровно per_source чанков на каждый source.

    Фильтр: token_count >= min_tokens (исключаем footnotes / mini-chunks),
    seed=42 для воспроизводимости.
    """
    rng = random.Random(RANDOM_SEED)
    sampled: list[Chunk] = []
    for sid in sorted(by_source):
        if only and not any(sub in sid for sub in only):
            continue
        candidates = [c for c in by_source[sid] if c.token_count >= min_tokens]
        if not candidates:
            continue
        n = min(per_source, len(candidates))
        sampled.extend(rng.sample(candidates, n))
    return sampled


async def _generate_one(chunk: Chunk, llm) -> dict | None:
    user = USER_PROMPT_TEMPLATE.format(
        source_title=chunk.source_title,
        section_path=chunk.section_path or "(без раздела)",
        text=_truncate_for_prompt(chunk.text),
    )
    sys_prompt = _build_system_prompt("русском" if chunk.language == "ru" else "английском")
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user},
    ]
    for attempt in range(3):
        try:
            resp = await llm.ainvoke(messages)
            content = resp.content if hasattr(resp, "content") else str(resp)
            parsed = _parse_response(content)
            q = (parsed.get("question") or "").strip()
            ans = (parsed.get("answer_summary") or "").strip()
            if not q or len(q) < 10:
                return None
            return {"question": q, "answer_summary": ans}
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                logging.warning("generate failed for %s: %s", chunk.id, e)
                return None
            await asyncio.sleep(2 * (attempt + 1))


async def generate_dataset_async(
    chunks: list[Chunk],
    *,
    model: str,
    concurrency: int,
) -> list[dict]:
    llm = get_chat_model(provider="hydra", model=model, temperature=0.3)
    sem = asyncio.Semaphore(concurrency)
    results: list[dict | None] = [None] * len(chunks)
    done = [0]
    total = len(chunks)

    async def worker(i: int, c: Chunk):
        async with sem:
            qa = await _generate_one(c, llm)
            if qa is not None:
                results[i] = {
                    "id": f"{c.id}__q",
                    "question": qa["question"],
                    "expected_chunk_id": c.id,
                    "expected_source_id": c.source_id,
                    "source_title": c.source_title,
                    "section_path": c.section_path,
                    "language": c.language,
                    "block": c.block,
                    "answer_summary": qa["answer_summary"],
                }
            done[0] += 1
            if done[0] % 20 == 0 or done[0] == total:
                logging.info("generated %d/%d", done[0], total)

    await asyncio.gather(*(worker(i, c) for i, c in enumerate(chunks)))
    return [r for r in results if r is not None]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", default="v1", help="Имя выходного файла: v1 → datasets/rag_eval/v1.jsonl")
    p.add_argument("--per-source", type=int, default=4, help="Сколько чанков на каждый source (4 × 25 = 100)")
    p.add_argument("--only", help="Подстрока source_id для фильтра (тест mode)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--min-tokens", type=int, default=500)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("build_rag_eval")

    only = [s.strip() for s in args.only.split(",")] if args.only else None

    by_source = load_chunks_grouped()
    log.info("Загружено чанков по источникам: %d", len(by_source))

    sampled = sample_chunks(
        by_source, per_source=args.per_source, only=only, min_tokens=args.min_tokens
    )
    log.info(
        "Sampled %d чанков (%d per source × %d source, min_tokens=%d)",
        len(sampled), args.per_source, len(by_source), args.min_tokens,
    )

    log.info("Генерация Q через %s (concurrency=%d)...", args.model, args.concurrency)
    qa_items = asyncio.run(generate_dataset_async(sampled, model=args.model, concurrency=args.concurrency))
    log.info("Сгенерировано %d/%d вопросов", len(qa_items), len(sampled))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.version}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for item in qa_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    log.info("Сохранено: %s (%d вопросов)", out_path, len(qa_items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
