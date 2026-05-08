# Эталонные датасеты для оценки

Каждый датасет обслуживает один eval-скрипт из [scripts/eval/](../scripts/eval/). Описание метрик — в [docs/experiments/design.md](../docs/experiments/design.md).

> Файлы наполняются по мере реализации соответствующих PR. Здесь — формат каждого.

## `rag_qa.jsonl`

Цель: оценка retriever'а (hit@k, MRR, recall@k).

Формат (одна строка = один пример):
```json
{
  "id": "rag_qa_0001",
  "question": "Каковы прогнозы OPEC по спросу на нефть в 2026 году?",
  "language": "ru",
  "expected_chunks": ["opec_momr_2026_03_p12_chunk_3", "opec_momr_2026_03_p13_chunk_1"],
  "expected_report": "OPEC MOMR March 2026",
  "answerable": true,
  "tags": ["opec", "demand", "forecast"]
}
```

Размер: 30-50 примеров. Размечается вручную после индексации корпуса.

## `routing.jsonl`

Цель: оценка узла classify_intent (accuracy, F1).

Формат:
```json
{
  "id": "routing_0001",
  "query": "Какая цена Brent сейчас?",
  "expected_route": "web",
  "rationale": "current price requires web — no static report has live quotes"
}
```

Возможные значения `expected_route`: `rag`, `web`, `forecast`, `oos` (out-of-scope), `mixed` (требуется комбинация).

Размер: 50-100 примеров.

## `citations_gold.jsonl`

Цель: оценка citations validator (precision, recall).

Формат:
```json
{
  "id": "citations_0001",
  "answer": "По данным [Отчёт OPEC MOMR, март 2026], спрос вырастет на 1.4 млн б/с. Reuters сообщает о новых санкциях.",
  "rag_chunks": [
    {"id": "opec_momr_2026_03_p12_chunk_3", "text": "..."}
  ],
  "web_results": [
    {"hostname": "reuters.com", "title": "...", "url": "..."}
  ],
  "valid_citations": ["Отчёт OPEC MOMR, март 2026", "Reuters"],
  "invalid_citations": []
}
```

Размер: 30+ примеров, включая sintetic с галлюцинациями (для testing recall).

## `forecast_history.csv`

Цель: бектест прогноза цен.

Формат: стандартный yfinance CSV
```csv
Date,Open,High,Low,Close,Adj Close,Volume
2020-01-02,66.42,66.93,66.13,66.25,66.25,...
...
```

Источник: `yfinance.download("BZ=F", start="2020-01-01", end="2026-05-01")`.

Скрипт обновления: TBD в `feature/forecast` PR.

## `e2e_dialogues.jsonl`

Цель: оценка системы целиком (success rate, citation correctness, structure adherence, refusal rate). Используется в [scripts/eval/eval_e2e.py](../scripts/eval/eval_e2e.py).

Формат (одна строка = один диалог):
```json
{
  "id": "e2e_0001",
  "scenario_type": "rag_only",
  "language": "ru",
  "held_out": false,
  "messages": [
    {"role": "user", "content": "Что говорит OPEC о квотах добычи в марте 2026?"}
  ],
  "expected_behavior": {
    "should_use_rag": true,
    "should_use_web": false,
    "should_call_forecast": false,
    "expected_refusal": false,
    "expected_keywords": ["OPEC", "кво"],
    "expected_min_citations": 1,
    "must_cite_sources": ["OPEC MOMR"],
    "rubric": [
      "Содержит конкретные цифры из отчёта",
      "Источник указан корректно с датой",
      "Не делает прогнозов от себя"
    ]
  }
}
```

**Поля:**

- `scenario_type` — один из: `rag_only`, `web_only`, `rag_plus_web`, `forecast`, `out_of_scope`, `multi_tool`, `follow_up`, `unknown_with_hypothesis` (запрет на «нет данных»).
- `held_out` — true для зафиксированного финального замера (не итерируем промпт на этих кейсах). При корпусе из 10 диалогов: 8 dev / 2 held-out.
- `messages` — список turn'ов в формате OpenAI/Anthropic. Single-turn = один user message; для `follow_up` — два или больше.
- `expected_behavior.expected_keywords` — substring matches в финальном ответе (case-insensitive, частичный — ловит «квоты»/«квот»/«квотами» через корень «кво»). Базовая, дешёвая проверка.
- `expected_min_citations` — минимальное число цитат **в формате** RAG/Web/Forecast в финальном ответе. Считаются [citation парсерами](../nefteboros/citations/patterns.py), без сверки с tool outputs (e2e оценивает финальный итог, не глубину тулов — для семантической сверки см. `eval_citations.py` на `citations_gold.jsonl`).
- `should_use_rag` / `should_use_web` / `should_call_forecast` — boolean'ы. Используются для citation tool-selection match: если `should_use_rag=true`, в ответе должна быть RAG-цитата в формате; аналогично для web/forecast.
- `must_cite_sources` — opt'ональные substring'и в `source_title`, которые должны быть процитированы. В e2e не задействовано (overhead semantic сверки), задел для `eval_citations.py`.
- `rubric` — текстовые критерии для optional LLM-as-judge. На deterministic baseline не используется (substring + structural только).

**Состав корпуса (50 диалогов, baseline для нахождения проблемных кейсов):**

| Категория | Кол-во | Held-out | Примечание |
|---|---:|---:|---|
| `rag_only` | 8 | 1 | Разные источники из manifest (OPEC, Bruegel, Энергостратегия, Новатэк, IEA, Газпром) |
| `web_only` | 5 | 1 | Spot-цены, свежие новости, RU + EN |
| `rag_plus_web` | 1 | 0 | Канон ТЗ §4.6 |
| `forecast` | 8 | 1 | Brent/WTI/Urals/ESPO/HH/TTF, разные горизонты, +1 рефьюз на 24m |
| `out_of_scope` | 7 | 1 | Погода, крипта, FX, юрист, оценки лиц, инвест-рекомендация, тривиальный |
| `multi_tool` | 8 | 1 | RAG+forecast, forecast+web, RAG+web, тройные комбо |
| `follow_up` | 4 | 1 | Двухтурные с переиспользованием контекста |
| `unknown_with_hypothesis` | 4 | 1 | Запрет на «нет данных» — структурная гипотеза |
| `adversarial` | 5 | 2 | «Без CI», prompt injection, торговый сигнал, social engineering, «скажи нет данных» |
| **Всего** | **50** | **9** | dev: 41, held-out: 9 (~18%) |

Канон ТЗ §4.6 — диалоги 1-5 (по одной строке на категорию).

При расширении до 100+ — балансировать: усилить adversarial (защита от prompt injection из C1/C2) и conflict cases (RAG vs web расхождения).
