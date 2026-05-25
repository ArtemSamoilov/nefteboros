# 2026-05-25 — Self-reflection: advisory саморазвитие (без самомодификации)

## Задача

ТЗ требует, чтобы агент на Ouroboros **обоснованно назывался саморазвивающимся**.
В v1.0.0 подсистемы самомодификации намеренно выпилены (changelog
2026-05-04-rip-self-modify) — для фин-аналитика самомодификация кода/промпта =
риск. Нужно вернуть МИНИМАЛЬНЫЙ, некритичный, ЧЕСТНЫЙ механизм саморазвития.

Решение (различение, снимающее риск): саморазвитие через **рефлексию ≠
самомодификацию кода**. Возвращаем только рефлексию (агент анализирует свою работу
и *предлагает* улучшения), НЕ самомодификацию (агент НЕ переписывает себя). См.
ADR-0029. Ветка `feature/self-reflection`, Python 3.12.

## Что сделано

### Пакет `nefteboros/self_reflection/` (изолированный, advisory)

- `schema.py` — `TraceView` (нормализованный трейс), `ReflectionItem`,
  `BacklogEntry` (`applied` всегда False — safety-маркер).
- `sources.py` — READ-ONLY источники: `JsonlTraceSource` (первичный, читает
  `metrics/runs/*/trace.jsonl`) + `LangfuseTraceSource` (best-effort, graceful
  откат на JSONL). Не касаются `chat.jsonl`/analyst-контекста.
- `detectors.py` — детерминированные сигналы из трейсов БЕЗ LLM: error-rate,
  горячие узлы-ошибки, latency/cost-перцентили, refusal-rate, citation-rate (через
  продовый `nefteboros.citations`), структурные прокси (synthesize без инструмента;
  synthesize без `validate_citations`). + `heuristic_items` — rule-based floor.
- `reflect.py` — оркестратор: трейсы → сигналы → LLM-синтез (модель резолвится как
  у advisory-ревью: `OUROBOROS_REFLECTION_MODEL` → `OUROBOROS_MODEL_LIGHT` →
  `SETTINGS_DEFAULTS`) → парсинг JSON → advisory-items. LLM первичный, heuristic —
  graceful floor. LLMClient импортируется лениво (чистый импорт пакета).
- `backlog.py` — durable JSONL-стор `data/self_improvement/backlog.jsonl`, dedup по
  fingerprint. Облегчённая версия выпиленного `improvement_backlog.py`: сохранён
  принцип advisory+dedup+provenance, ВЫРЕЗАН `format_backlog_digest` (инжект в
  контекст — повтор дефекта ADR-0027).

### CLI `scripts/self_reflect.py`

`run` / `status` / `show-backlog`. `run` гейтится флагом
`OUROBOROS_SELF_REFLECTION` (default OFF; `--force` для разового прогона). Стиль
bootstrap как у `scripts/forecast.py`. Graceful: ошибка не роняет процесс.

### Env-флаг

`OUROBOROS_SELF_REFLECTION` (default OFF) — прод (путь ответа агента) от рефлексии
не зависит. `OUROBOROS_REFLECTION_MODEL` — override модели.

## Верификация

Окружение: Python 3.12 (`.venv` через uv), creds из `.env` (GigaChat/Hydra).

### Тесты — `tests/test_self_reflection.py` (17 passed)

Покрытие: парсинг трейсов; детекторы на реальных числах (error_rate, refusal_rate,
citation_rate, структурные прокси); dedup backlog'а; LLM-пайплайн на stub'е
транспорта (как `test_advisory_observability.py`); **safety-проверки по AST**:
(а) пакет НЕ импортирует analyst-граф/контекст/agent/консолидатор; (б) нет
строкового литерала `chat.jsonl` в коде; (в) код нигде не выставляет
`applied=True`; (г) прод-код (`server.py`, `ouroboros/*`, `nefteboros/graphs/*`,
`nefteboros/observability/*`) НЕ импортирует `self_reflection`; (д) AST-валидность
всех новых файлов; (е) env-флаг default OFF; graceful при сбое LLM.

### Реальный прогон на реальных трейсах (детерминированный путь, без ключей)

`run --no-llm` на локальных `metrics/runs/*/trace.jsonl` (50 реальных трейсов) →
реальные структурные находки (tool_skip, citation_node_gap) с evidence-trace_id.
Доказывает: детекторы работают на реальных данных без LLM.

### Реальный LLM-прогон (демо `examples/self_reflection/`)

`run` с `openai-compatible::gpt-oss-120b` (Hydra) на 8 контент-богатых
sample-трейсах (ответы из eval-фикстур проекта) → **5 реальных LLM-находок**
(`source=llm`, `applied=false`): retry/fallback для forecast_call, обязательный
инструмент перед synthesize, оптимизация латентности p95=11.8с, разделение флагов
отказа/цитат. Один item — рефлексия агента над false-positive в **собственном**
структурном детекторе (citation_node_gap на refusal'ах). Демо воспроизводимо
(README, оба пути: keyless `--no-llm` и с LLM).

## Что НЕ сделано (явно, см. ADR-0029 «Не в scope»)

- **Auto-apply** — сознательно нет (safety-граница; человек в петле).
- **Самомодификация** кода/промпта; возврат `consciousness`/`deep_self_review`.
- **Per-request** рефлексия — только по CLI-команде.
- **Cron / раз в N сессий** — точка расширения, инфраструктуру не строим.
- **Langfuse-путь не верифицирован** на живых ключах (их нет) — реализован
  best-effort с graceful-откатом, протестирован откат на JSONL. JSONL —
  тестируемый/демо-путь.
- **Semantic dedup** — сейчас по точному fingerprint; лёгкий churn near-dup между
  прогонами для advisory-backlog'а приемлем.

## Файлы

- Добавлено: `nefteboros/self_reflection/{__init__,schema,sources,detectors,reflect,backlog}.py`,
  `scripts/self_reflect.py`, `tests/test_self_reflection.py`,
  `docs/adr/0029-self-reflection.md`, этот changelog,
  `examples/self_reflection/{build_sample_traces.py,sample_traces.jsonl,backlog.demo.jsonl,README.md}`,
  `data/self_improvement/.gitkeep`.
- Изменено: `.gitignore` (живой backlog игнорируется, демо коммитится).

## Связанные

- ADR-0029 (self-reflection), ADR-0027 (eval-dialogue-isolation — урок изоляции),
  ADR-0024 (observability-langfuse — переиспользуемый источник трейсов),
  ADR-0001 (fork-ouroboros).
- changelog 2026-05-04-rip-self-modify (что и почему выпилено),
  2026-05-24-observability-eval-robustness (PR #78, починка observability).
- Прецедент advisory-паттерна: `ouroboros/tools/claude_advisory_review.py`.
