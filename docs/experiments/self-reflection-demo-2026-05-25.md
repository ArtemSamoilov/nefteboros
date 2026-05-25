# Self-reflection — демо-прогон advisory backlog (v2.4.0)

- **Дата:** 2026-05-25
- **Связано:** ADR-0029 (self-reflection), PR #84, ADR-0027 (изоляция диалогов/трейсов)
- **Статус:** демонстрационный прогон, зафиксирован по запросу координатора

## Что это

Механизм саморазвития v2.4.0: агент читает трейсы своих прошлых ответов → LLM
рефлексирует над паттернами → пишет **advisory-backlog** самоулучшений. Ключевое:

- **Не применяет автоматически** (`applied=false`) — решает человек. Это осознанная
  safety-граница для ассистента, работающего с финансовыми данными.
- **Изолирован от пути ответа** (ADR-0027/0029): пакет `nefteboros/self_reflection/`
  не импортирует analyst-граф/контекст/agent; `server.py`/`web/` не импортируют пакет;
  читает трейсы read-only, в контекст агента ничего не инжектит.
- **Ouroboros-петля** в честной форме: наблюдение (трейсы) → рефлексия → backlog.

## Как вызывается

**CLI, не из интерфейса.** Из UI (web) механизм не вызывается by-design — это отдельный
офлайн-цикл, а не действие в чате. `OUROBOROS_SELF_REFLECTION` по умолчанию `OFF` (прод от
пакета не зависит).

```bash
# Полный прогон с LLM-синтезом (модель через Hydra/OpenAI-compatible):
OUROBOROS_SELF_REFLECTION=1 python scripts/self_reflect.py run --force \
  --traces examples/self_reflection/sample_traces.jsonl \
  --model openai-compatible::gpt-oss-120b

# Показать накопленный backlog:
python scripts/self_reflect.py show-backlog
```

Источник трейсов: `JsonlTraceSource` (первичный, офлайн — `metrics/runs/<ts>/trace.jsonl`),
`LangfuseTraceSource` (best-effort при наличии ключей). **Без LLM-ключей команда тоже
отрабатывает** — детерминированный heuristic floor выдаёт находки попроще (честное свойство
«работает без облака»).

## Прогон 2026-05-25

Параметры: sample-трейсы (`examples/self_reflection/sample_traces.jsonl`), LLM
`openai-compatible::gpt-oss-120b`, источник = jsonl. Сигналы по выборке: `error_rate=0.125`,
`p95=11800мс`, `refusal_rate=0.143`, `citation_rate=0.714`.

**Результат: 7 advisory-находок, все `source=llm`, `applied=false`.**

| severity | category | наблюдение → предложение |
|---|---|---|
| high | error | «Дай прогноз Brent на 10 лет» упал на `forecast_call` (11800мс) → ограничить горизонт прогноза + валидация входных параметров до вызова |
| high | routing | после `forecast_call` отсутствует `validate_citations` → обязать узел + fallback к synthesize при непрохождении |
| high | routing | `validate_citations` присутствует во всех forecast-трассах, кроме demo_error → авто-проверка наличия узла перед `synthesize` |
| medium | citations | ungrounded-ответ без citation-узла (флаги `citation_node_gap`, `no_citation`, `tool_skip`) → запретить `synthesize` без RAG/web-опоры |
| medium | coverage | `citation_rate=0.714` (~30% ответов без ссылок) → целевой порог ≥0.95 + авто-доп.поиск |
| medium | latency | `p95=11800мс` (p50=2400) → кэш/async/предзагрузка тяжёлых узлов, SLA ≤1500мс |
| low | refusal | `refusal_rate=0.143` → более тонкая градация out-of-scope в intent-классификации |

> Находки — это **предложения агента самому себе**, не применённые изменения.
> Применение/закрытие каждой — решение человека.

## Замечания

- Прогон на `sample_traces.jsonl` (воспроизводимо). Live-трейсы прод-сервера
  (`trace.jsonl`) на момент прогона пусты — известный gap JSONL-tracer'а
  (см. ADR-0027 §follow-up), на демонстрацию не влияет.
- Артефакт находок и заготовленные трейсы — в `examples/self_reflection/`.
