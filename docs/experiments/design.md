# Дизайн экспериментов и метрик

> Документ описывает, какие подграфы системы измеряем, какими метриками, на каких датасетах, и как сравниваем варианты. По мере появления реальных результатов — добавляются ссылки на конкретные runs в `metrics/runs/`.

## Зачем измеряем

ТЗ требует осознанные решения по выбору технологий и моделей. Без метрик «GigaChat лучше, чем Kimi» — необоснованное утверждение. С метриками — это таблица: latency, cost, faithfulness, accuracy на нашем golden set'е.

Цель экспериментов — не выбрать «лучшую» модель/метод раз и навсегда, а **показать процесс принятия решений** на тестовом задании.

## Подграфы системы и их метрики

### 1. RAG retriever (`nefteboros/rag/`)

**Что меряем:** насколько точно retriever находит правильный чанк под вопрос.

**Метрики:**
- `hit@k` для k=1, 3, 5 — доля вопросов, для которых правильный чанк попал в top-k
- `MRR` (Mean Reciprocal Rank) — средняя обратная позиция первого правильного чанка
- `recall@k` — доля релевантных чанков, попавших в top-k

**Датасет:** `datasets/rag_qa.jsonl` — 30-50 примеров вида `{"question": str, "expected_chunks": [chunk_id], "report": str}`. Размечается вручную после индексации корпуса.

**Скрипт:** `scripts/eval/eval_rag.py`

**Сравниваем:**
- Эмбеддеры: BGE-M3 vs multilingual-e5-large vs paraphrase-multilingual-mpnet
- Размер чанка: 256 / 512 / 1024 токена
- Chunk overlap: 0 / 64 / 128 токенов
- Стратегия: семантическая разбивка vs sliding window

### 2. Routing (`nefteboros/graphs/analyst_graph.py`, узел classify_intent)

**Что меряем:** правильно ли граф решает, какой ветке отдать запрос (rag / web / forecast / out-of-scope).

**Метрики:**
- `accuracy` — доля верно классифицированных запросов
- `F1` per class
- `confusion matrix`

**Датасет:** `datasets/routing.jsonl` — 50-100 примеров вида `{"query": str, "expected_route": "rag|web|forecast|oos"}`.

**Скрипт:** `scripts/eval/eval_routing.py`

**Сравниваем:**
- LLM-классификатор vs zero-shot prompt vs few-shot prompt
- Разные модели (GigaChat Lite vs Max vs kimi-k2p6)

### 3. Citations validator (`nefteboros/citations/`)

**Что меряем:** анти-галлюцинация — насколько точно валидатор ловит выдуманные источники.

**Метрики:**
- `precision` — из заявленных источников сколько реально подтверждено чанками
- `recall` — из реально подтверждённых сколько отмечено в ответе
- `false-attribution rate` — доля цитат, где источник назван неверно

**Датасет:** `datasets/citations_gold.jsonl` — 30 примеров `{"answer": str, "rag_chunks": [...], "valid_citations": [...]}`.

**Скрипт:** `scripts/eval/eval_citations.py`

### 4. Forecast (`nefteboros/forecast/`)

**Что меряем:** качество прогноза цены Brent на 1/3/6 месяцев.

**Метрики:**
- `MAPE` (Mean Absolute Percentage Error) — основная метрика
- `RMSE`, `MAE`
- `coverage` 80% и 95% доверительных интервалов — доля точек, попавших в CI

**Датасет:** `datasets/forecast_history.csv` — исторические цены Brent (yfinance, тикер `BZ=F`), бектест по rolling window.

**Скрипт:** `scripts/eval/eval_forecast.py`

**Сравниваем:**
- ARIMA vs SARIMA vs Prophet vs ETS vs naive baseline (last value, MA)
- Разные горизонты: 1m / 3m / 6m

### 5. LLM models (`nefteboros/llm/`)

**Что меряем:** сравниваем все доступные модели для задачи синтеза ответа аналитика.

**Метрики:**
- `latency_p50`, `latency_p95` — мс на запрос
- `cost_per_query` — рубли (для GigaChat) и USD (для остальных, конвертация по курсу)
- `faithfulness` — правда ли в ответе только то, что было в RAG-чанках (LLM-as-judge или ручная разметка)
- `helpfulness` — субъективная оценка качества (rubric)

**Датасет:** `datasets/e2e_dialogues.jsonl` (см. ниже)

**Скрипт:** `scripts/eval/eval_llm.py`

**Модели в сравнении:** GigaChat Max, GigaChat Ultra, kimi-k2p6, kimi-k2p5, glm-5p1, glm-5, deepseek-v4-pro, deepseek-v3p2, deepseek-v3p1, minimax-m2p7, gpt-oss-120b.

### 6. End-to-end (`scripts/eval/eval_e2e.py`)

**Что меряем:** качество системы целиком на golden dialogues.

**Метрики:**
- `success rate` — доля сценариев, где ответ соответствует rubric
- `citation correctness` — все ли цитаты валидны
- `latency_full` — время полного ответа (от запроса до финального текста)
- `fallback rate` — как часто скатываемся в «не знаю» / web для запросов, где RAG должен был справиться

**Датасет:** `datasets/e2e_dialogues.jsonl` — 5+ демо-сценариев из ТЗ §4.6 + дополнительные edge-cases.

## Дашборд

`scripts/eval/make_dashboard.py` собирает результаты всех `metrics/runs/*.json` → один markdown-отчёт в `docs/experiments/results.md` (на момент финального PR).

## Принципы экспериментов

1. **Воспроизводимость:** каждый run сохраняется как `metrics/runs/<date>_<model>_<commit-sha>.json` со всеми гиперпараметрами.
2. **Изоляция:** каждый подграф измеряется отдельно от остальных. Если e2e упал — диагностика по отдельным метрикам.
3. **Baseline'ы:** для каждой метрики есть «глупый» бейзлайн (random, last-value, naive prompt). Без него нельзя оценить, сколько даёт умная версия.
4. **Honest reporting:** в `docs/experiments/*.md` фиксируем не только лучший результат, но и что не сработало (failed experiments).

## Что НЕ меряем (явно)

- Subjective UX (вне scope тестового)
- Production load (не нагрузочное тестирование)
- Robustness против adversarial prompts (отдельная задача)
- Энергопотребление / углеродный след

## Roadmap метрик по дням

| День | Что меряем |
|---|---|
| 3-4 | RAG hit@k (после первой индексации корпуса) |
| 4 | Routing accuracy (после прототипа classifier) |
| 5 | Forecast MAPE (после ARIMA + Prophet прототипа) |
| 6 | Citations precision/recall (после prototypа validator) |
| 7 | LLM comparison на e2e (после интеграции графа) |
| 8 | Финальный dashboard, обновление всех experiments/*.md |
