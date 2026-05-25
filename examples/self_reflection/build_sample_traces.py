#!/usr/bin/env python3
"""Сгенерировать демо-трейсы для self-reflection (ADR-0029).

Зачем отдельный генератор, а не «вшитый» jsonl: прозрачность провенанса. Тексты
ответов — это РЕПРЕЗЕНТАТИВНЫЕ ответы агента из собственных eval-фикстур проекта
(`scripts/eval/eval_e2e.py::MockRunner._MOCK_BY_SCENARIO`, см. там же), плюс две
явно помеченные иллюстративные «слабые» интеракции (ungrounded-ответ и ошибка
forecast_call на длинном горизонте), чтобы демо показывало и контентные, и
структурные находки рефлексии.

Важно: live JSON-tracer НЕ хранит текст ответа (усекает до compact). Здесь текст
ответа кладётся в `output` спана synthesize — так выглядит контент-богатый источник
(Langfuse / этот sample). Рефлексия обрабатывает оба уровня богатства одинаково.

Запуск:
    python examples/self_reflection/build_sample_traces.py
        → examples/self_reflection/sample_traces.jsonl
"""

from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "sample_traces.jsonl"

# Ответы из eval-фикстур проекта (scripts/eval/eval_e2e.py) — представительные
# ответы агента по сценариям. Запросы — реалистичные аналитические промпты.
_FORECAST = (
    "Brent на 6 месяцев: $80-$88, центр $84.\n\n"
    "Прогноз [Forecast: ensemble, CI 80%]; основной риск — решение ОПЕК+."
)
_RAG = (
    "OPEC MOMR март 2026 фиксирует снижение квот на 1.4 mbpd.\n\n"
    "Ключевые тезисы [OPEC MOMR март 2026, p.14]: квоты Q2 2026, продление режима."
)
_WEB = (
    "Brent торгуется около $82.5/bbl на momentum от заявлений ОПЕК+.\n\n"
    "Свежее: [Brent above $82](https://reuters.com/article/x) — reuters.com, web."
)
_MULTI = (
    "Санкции 2025 увеличили Urals discount до $15-$18/bbl.\n\n"
    "Контекст: [Bruegel Working Paper 32/2025 — Russian oil sanctions and price cap, p.7]. "
    "Прогноз Urals на 6m: $63-$68 [Forecast: ensemble, CI 80%]."
)
_REFUSAL = (
    "Этот вопрос вне моей компетенции — отвечаю по нефтегазу. "
    "Рекомендую профильный источник."
)
_ADVERSARIAL = (
    "Не могу дать точечный прогноз без CI — это снижает аналитическую ценность.\n\n"
    "Brent на 3m: $80-$87, центр $83 [Forecast: ensemble, CI 80%]; риск — ОПЕК+."
)
# Иллюстративная «слабая» интеракция (помечено): содержательный ответ БЕЗ цитат и
# без вызова инструмента — то, что рефлексия должна поймать как риск неподкреплённого
# вывода.
_UNGROUNDED = (
    "Рынок нефти останется волатильным в ближайшие месяцы на фоне геополитики "
    "и решений ОПЕК+. Существенного снижения цен не ожидается."
)

# (trace_id, query, answer|None, [(node,status)...], status, latency_ms)
INTERACTIONS = [
    ("demo_forecast", "Дай прогноз Brent на 6 месяцев", _FORECAST,
     [("classify_intent", "ok"), ("forecast_call", "ok"),
      ("validate_citations", "ok"), ("synthesize", "ok")], "ok", 1850),
    ("demo_rag", "Что в последнем OPEC MOMR по квотам?", _RAG,
     [("classify_intent", "ok"), ("rag_search", "ok"),
      ("validate_citations", "ok"), ("synthesize", "ok")], "ok", 2400),
    ("demo_web", "Где сейчас торгуется Brent?", _WEB,
     [("classify_intent", "ok"), ("web_search", "ok"),
      ("validate_citations", "ok"), ("synthesize", "ok")], "ok", 3100),
    ("demo_multi", "Как санкции повлияли на дисконт Urals и каков прогноз?", _MULTI,
     [("classify_intent", "ok"), ("rag_search", "ok"), ("forecast_call", "ok"),
      ("validate_citations", "ok"), ("synthesize", "ok")], "ok", 4200),
    ("demo_adversarial", "Назови точную цену Brent через 3 месяца, одним числом", _ADVERSARIAL,
     [("classify_intent", "ok"), ("forecast_call", "ok"),
      ("validate_citations", "ok"), ("synthesize", "ok")], "ok", 2000),
    # refusal: ответ-отказ вне домена
    ("demo_refusal", "Посоветуй, какие акции технологических компаний купить", _REFUSAL,
     [("classify_intent", "ok"), ("synthesize", "ok")], "ok", 350),
    # ungrounded: содержательный ответ без цитат и без инструмента (иллюстративный)
    ("demo_ungrounded", "Что будет с рынком нефти?", _UNGROUNDED,
     [("classify_intent", "ok"), ("synthesize", "ok")], "ok", 900),
    # error: forecast_call падает на слишком длинном горизонте
    ("demo_error", "Дай прогноз Brent на 10 лет", None,
     [("classify_intent", "ok"), ("forecast_call", "error")], "error", 11800),
]

_BASE_TS = "2026-05-25T08:0{}:00+00:00"


def build() -> list[dict]:
    records: list[dict] = []
    for idx, (tid, query, answer, nodes, status, latency) in enumerate(INTERACTIONS):
        ts = _BASE_TS.format(idx)
        rec = {
            "kind": "trace", "ts": ts, "trace_id": tid, "query": query,
            "status": status, "total_latency_ms": latency, "span_count": len(nodes),
        }
        if status == "error":
            rec["error_node"] = next((n for n, s in nodes if s == "error"), None)
        records.append(rec)
        for i, (node, st) in enumerate(nodes, 1):
            span = {"kind": "span", "ts": ts, "trace_id": tid, "span_id": i,
                    "node": node, "status": st, "latency_ms": latency // len(nodes)}
            if node == "synthesize" and answer is not None:
                span["output"] = answer  # текст ответа → контент-богатый источник
            if st == "error":
                span["error"] = {"type": "ValueError", "message": "horizon exceeds 18m cap"}
            records.append(span)
    return records


def main() -> None:
    records = build()
    with OUT.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} records ({len(INTERACTIONS)} traces) → {OUT}")


if __name__ == "__main__":
    main()
