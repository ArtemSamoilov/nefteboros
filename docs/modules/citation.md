# Модуль: Citation verification (D6)

Анти-галлюцинационный валидатор цитат: парсит метки `[Отчёт OPEC MOMR, март 2026]`, `[Источник: reuters.com, web]`, `[Forecast: ou_regime, scenario=bear, CI 80%]` в ответе синтеза, сверяет каждую с известными источниками из текущего контекста и помечает «нерасшифрованные».

## Точка входа

- `nefteboros/citations/validator.py` — `validate(text, sources) -> CitationReport`.
- `nefteboros/citations/patterns.py` — regex-патёрны для трёх типов цитат (RAG / Web / Forecast).
- Узел графа: `nefteboros/graphs/nodes/validate.py` — `validate_citations(state)`. Запускается **после** synthesize, перед END (`analyst_graph.py:153`).

## Поток

1. Распарсить ответ synthesize regex'ами из `patterns.py`:
   - `RAG_PATTERN` — `[Отчёт <source>, <date>]`
   - `WEB_PATTERN` — `[Источник: <domain>, web]`
   - `FORECAST_PATTERN` — `[Forecast: <model>, scenario=<name>, CI <level>]` (опциональная `scenario` для legacy backtest baseline).
2. Сверить каждую цитату с `state.sources` (документы из RAG / web results / forecast metadata).
3. Сформировать `CitationReport`:
   - `cited_sources` — расшифрованные.
   - `unverified` — упомянуты в тексте, но нет в `state.sources`.
   - `missing` — есть в `state.sources`, но не процитированы.
   - `precision`, `recall`, `false_attribution_rate` — для текущего ответа.

## Входы / выходы

**Вход:** `state.synthesis: str` (текст ответа), `state.sources: SourceContext` (RAG chunks + web results + forecast metadata).

**Выход:** `state.citation_report: CitationReport`. В финальной выдаче клиенту попадает либо чистый `synthesis`, либо `synthesis + warning` если есть `unverified`.

## Citation формат

- `[Отчёт OPEC MOMR, март 2026]` — RAG.
- `[Источник: reuters.com, web]` — web.
- `[Forecast: ou_regime, scenario=bear, CI 80%]` — forecast (production); `scenario=` обязателен, регекс — в [ADR-0023 §Q4](../adr/0023-forecast-ensemble-map.md). Legacy backtest baseline без `scenario` тоже матчится (см. ADR-0024 §A4).
- Форма закреплена в системном промпте: [ADR-0019](../adr/0019-system-prompt-analyst.md), `prompts/SYSTEM.md`.

## Ключевые ADR

- [ADR-0019](../adr/0019-system-prompt-analyst.md) — citation format в системном промпте.
- [ADR-0023 §Q4](../adr/0023-forecast-ensemble-map.md) — финальный citation regex.
- [ADR-0024 §A4](../adr/0024-ou-regime-forecast.md) — расширение FORECAST_PATTERN под `scenario=`.

## Метрики

**Span есть только на узле** (`validate_citations` — `analyst_graph.py:130-132`). Внутренние шаги парсинга / matching НЕ инструментированы — нет отдельных sub-spans.

**Eval скрипт `scripts/eval/eval_citations.py` — placeholder.** Содержит `raise NotImplementedError`. То есть отдельной off-line валидации цитат **не запущено**, метрики `precision/recall/false_attribution_rate` собираются только онлайн (на узле графа) и косвенно через `eval_e2e.py`:
- `citation_correctness` в e2e отражает: «упоминаемые источники существуют в контексте».
- В baseline на 100 диалогах (Track D-base, см. memory) `cite=0.181` — низкий показатель, основной gap агента.

Сигнал для координатора: **`eval_citations.py` нужно довести до рабочего состояния**, иначе D6 не имеет offline regression-тестов.

## Известные ограничения

- Anti-hallucination валидатор работает только для **RAG**-цитат на момент 2026-05-11. Web-цитаты (`[Источник: <domain>, web]`) и forecast метки проверяются только на синтаксис, не на content fidelity (см. README старая редакция, секция возможностей).
- Domain spoof attack не рассмотрен: если LLM напишет `[Источник: reuters.com, web]` со ссылкой на факт, которого reuters не публиковал, validator пропустит — только domain matching, не content matching.
- Track D-base baseline на 100 диалогах показал `cite=0.181` — это сигнал к расширению coverage и/или повышению агента в дисциплине цитирования (промпт-инженеринг + few-shot).
