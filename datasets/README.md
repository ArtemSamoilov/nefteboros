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

Цель: оценка системы целиком (success rate, citation correctness, latency).

Формат:
```json
{
  "id": "e2e_0001",
  "scenario_type": "rag_only",
  "user_query": "Что говорит OPEC о квотах добычи в марте 2026?",
  "language": "ru",
  "expected_behavior": {
    "should_use_rag": true,
    "should_use_web": false,
    "should_call_forecast": false,
    "must_cite_sources": ["OPEC MOMR"],
    "rubric": [
      "Содержит конкретные цифры из отчёта",
      "Источник указан корректно с датой",
      "Не делает прогнозов от себя"
    ]
  }
}
```

Минимум 5 сценариев из ТЗ §4.6:
1. Ответ на основе отчёта (`scenario_type: "rag_only"`)
2. Ответ на основе веб-поиска (`scenario_type: "web_only"`)
3. Комбинированный ответ (`scenario_type: "rag_plus_web"`)
4. Вызов forecast tool (`scenario_type: "forecast"`)
5. Out-of-scope (`scenario_type: "out_of_scope"`)

Плюс edge cases (3-5 штук для надёжности): неоднозначный запрос, несвежие данные, смешанные источники.
