# Модуль: Citation verification (D6)

Анти-галлюцинационный валидатор цитат: парсит метки `[Отчёт OPEC MOMR, март 2026]`, `[Источник: reuters.com, web]`, `[Forecast: ou_regime, scenario=bear, CI 80%]` в ответе синтеза, сверяет каждую с известными источниками из текущего контекста и помечает «нерасшифрованные».

## Точка входа

- `nefteboros/citations/validator.py` — `validate(text, sources) -> CitationReport`.
- `nefteboros/citations/patterns.py` — регекс-паттерны для трёх типов цитат (RAG / Web / Forecast).
- Узел графа: `nefteboros/graphs/nodes/validate.py` — `validate_citations(state)`. Запускается **после** synthesize, перед END (`analyst_graph.py:153`).

## Поток

1. Распарсить ответ синтеза регексами из `patterns.py`:
   - `RAG_PATTERN` — `[Отчёт <source>, <date>]`
   - `WEB_PATTERN` — `[Источник: <domain>, web]`
   - `FORECAST_PATTERN` — `[Forecast: <model>, scenario=<name>, CI <level>]` (опциональный `scenario` для legacy baseline бэктеста).
2. Сверить каждую цитату с `state.sources` (документы из RAG / результаты веб-поиска / metadata прогноза).
3. Сформировать `CitationReport`:
   - `cited_sources` — расшифрованные.
   - `unverified` — упомянуты в тексте, но нет в `state.sources`.
   - `missing` — есть в `state.sources`, но не процитированы.
   - `precision`, `recall`, `false_attribution_rate` — для текущего ответа.

## Входы / выходы

**Вход:** `state.synthesis: str` (текст ответа), `state.sources: SourceContext` (чанки RAG + результаты веба + metadata прогноза).

**Выход:** `state.citation_report: CitationReport`. В финальной выдаче клиенту попадает либо чистый `synthesis`, либо `synthesis + warning`, если есть `unverified`.

## Формат цитат

- `[Отчёт OPEC MOMR, март 2026]` — RAG.
- `[Источник: reuters.com, web]` — веб.
- `[Forecast: ou_regime, scenario=bear, CI 80%]` — прогноз (production); `scenario=` обязателен, регекс — в [ADR-0023 §Q4](../adr/0023-forecast-ensemble-map.md). Legacy baseline бэктеста без `scenario` тоже матчится (см. ADR-0024 §A4).
- Форма закреплена в системном промпте: [ADR-0019](../adr/0019-system-prompt-analyst.md), `prompts/SYSTEM.md`.

## Ключевые ADR

- [ADR-0019](../adr/0019-system-prompt-analyst.md) — формат цитат в системном промпте.
- [ADR-0023 §Q4](../adr/0023-forecast-ensemble-map.md) — финальный регекс цитат.
- [ADR-0024 §A4](../adr/0024-ou-regime-forecast.md) — расширение FORECAST_PATTERN под `scenario=`.

## Метрики

**Span есть только на узле** (`validate_citations` — `analyst_graph.py:130-132`). Внутренние шаги парсинга и сопоставления НЕ инструментированы — отдельных под-span'ов нет.

**Eval-скрипт `scripts/eval/eval_citations.py` — заглушка.** Содержит `raise NotImplementedError`. То есть отдельной offline-валидации цитат **не запущено**, метрики `precision/recall/false_attribution_rate` собираются только онлайн (на узле графа) и косвенно через `eval_e2e.py`:
- `citation_correctness` в e2e отражает «упоминаемые источники существуют в контексте».
- В baseline на 100 диалогах (Track D-base) `cite=0.181` — низкий показатель, основной gap агента.

Сигнал координатору: **`eval_citations.py` нужно довести до рабочего состояния**, иначе D6 не имеет offline regression-тестов.

## Известные ограничения

- Анти-галлюцинационный валидатор работает только для **RAG**-цитат на момент 2026-05-11. Веб-цитаты (`[Источник: <domain>, web]`) и forecast-метки проверяются только синтаксически, не на достоверность содержания.
- Атака подмены домена не рассмотрена: если LLM напишет `[Источник: reuters.com, web]` со ссылкой на факт, которого Reuters не публиковал, валидатор пропустит — сверяется только домен, не содержание.
- Track D-base baseline на 100 диалогах показал `cite=0.181` — это сигнал к расширению покрытия и/или повышению дисциплины цитирования у агента (промпт-инжиниринг + few-shot).
