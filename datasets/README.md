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
- `expected_min_citations` — минимальное число валидных цитат через [citations validator](../nefteboros/citations/). Для `out_of_scope` и `unknown_with_hypothesis` обычно 0.
- `must_cite_sources` — substring'и в `source_title` chunks/hits, которые **должны** быть процитированы (semantic).
- `rubric` — текстовые критерии для optional LLM-as-judge оценки. На baseline'е D6 не используется (deterministic substring + structural только).

**Состав корпуса (10 диалогов в первом baseline):**

5 ТЗ-сценариев (по §4.6):
1. `rag_only` — вопрос про отчёт
2. `web_only` — свежая новость / spot-цена
3. `rag_plus_web` — комбо
4. `forecast` — прогноз с CI
5. `out_of_scope` — вне нефтегаза

5 multi-tool / edge:
6. `multi_tool` — RAG + forecast (санкции и Urals)
7. `multi_tool` — forecast + web (новости + прогноз)
8. `multi_tool` — RAG + web (отчёт vs свежие новости)
9. `follow_up` — двухтурный диалог с переиспользованием первого ответа
10. `unknown_with_hypothesis` — запрос на стыке, агент даёт структурную гипотезу с маркировкой неопределённости (запрет на «нет данных» из roadmap B1)

При расширении до 30-50 — пропорционально увеличить multi-tool / adversarial / hedging кейсы.
