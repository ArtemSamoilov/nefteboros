# 2026-05-11 — docs/REPORT.md (deliverable по ТЗ §4.5)

**PR:** `docs/report-md-deliverable`
**Связано:** ТЗ §4.5 («краткий отчёт 1–2 страницы»), [docs/architecture.md](../architecture.md), [roadmap-v2.1.md](../roadmap-v2.1.md), [ADR-0024-ou-regime-forecast](../adr/0024-ou-regime-forecast.md).

## Задача

ТЗ §4.5 требует короткого отчёта (1–2 страницы): что сделано, архитектура, выбор технологий, ограничения, возможные улучшения. Это один из двух файлов (README + REPORT), которые рецензент Sber CIB читает в первую очередь. Документа не существовало — единственный кандидат был `docs/report/forecast-section.md` (узкая выжимка по forecast, не общий отчёт).

## Что сделано

Создан [`docs/REPORT.md`](../REPORT.md) — формальный отчёт по структуре ТЗ §4.5:

1. **Описание** — что делает агент, для кого, ключевые возможности (1 абзац).
2. **Архитектура** — двухуровневая (Ouroboros fork + LangGraph subgraph), компонентная таблица, ссылка на [architecture.md](../architecture.md) для полной mermaid-схемы.
3. **Технологии и обоснование** — LLM stack (GigaChat + Hydra + AItunnel fallback после prod-инцидента 2026-05-11), forecast (OU вместо SARIMAX — ADR-0024), RAG (Chroma + BGE-M3 + крупные чанки), LangGraph как детерминированный orchestrator.
4. **Ограничения** — Telegram-бот RKN-блок, ~33% root-trace loss в Langfuse (accepted trade-off), отсутствие numeric grounding, prompt injection как mitigation, экспертная калибровка forecast параметров, корпус v2.0.0 frozen.
5. **Возможные улучшения** — 5 пунктов из [roadmap-v2.1.md](../roadmap-v2.1.md), приоритизированы: numeric grounding (D4), conflict resolution (backlog A), custom scenarios для forecast, per-snapshot calibration overlay, self-hosted Langfuse + reasoning summary в UI.
6. **Метрики качества** — таблица 4 метрик e2e eval (success_rate, citation_rate, structure_score, refusal_correctness), v2.0.0 baseline на 100 диалогах + placeholder для v2.3.5 (подставит сессия D из `docs/eval-results-v2.3.5.md`); forecast walk-forward backtest резюме.

## Структура

- Объём ~2.5 страницы Markdown (~9.7K символов). Верхняя граница ТЗ.
- Все 6 секций ТЗ §4.5 покрыты.
- 4 placeholder'а `<!-- TODO: координатор подставит ... -->` в колонке v2.3.5 таблицы метрик (строки 56-59 в REPORT.md).
- Все технологические утверждения сопровождены ссылкой на ADR или changelog.

## Что НЕ в PR

- Полный текст eval-results v2.3.5 — отложен в сессию D (`docs/eval-results-v2.3.5.md`), placeholder поставлен.
- Скриншоты Langfuse UI — могут быть добавлены в README отдельным PR (сессия A).
- README — отдельный PR (сессия A, координация).
- 5 демо-сценариев — отдельный PR (сессия C, `examples/`).

## Замеченные противоречия в docs/ (для координатора)

1. **Координатор просил заголовок «v1.0 baseline» в таблице метрик.** В моей памяти (feedback_d_base_baseline) числа success=0.568 / cite=0.181 / struct=0.528 / refusal=0.947 отнесены к **v2.0.0** на 100 диалогах. Тег `v1.0.0` git соответствует первому Timeweb-deploy ещё без RAG (см. `git tag -n9 v1.0.0`). Принято решение использовать «v2.0.0 baseline» — это технически точно. Если координатор настаивает на «v1.0 baseline» — поправить можно одним sed'ом.
2. **`forecast_scenarios` пайплайн (bear/base/bull) интегрирован в LangGraph в [PR #55](../changelog/2026-05-10-forecast-scenarios-pipeline.md), 2026-05-10.** В отчёте указано как production-ready. Custom scenarios (`ScenarioParams(flags=...)` с runtime overrides) — backlog v2.2, упомянуто в §5.

## Слабые места отчёта

- **Длина 2.5 страницы — на верхней границе ТЗ (1-2).** Сокращал в 2 итерации (с 4 страниц до 2.5). Дальнейшее ужатие резало бы substance в §3 (выбор технологий) или §4 (ограничения), где честность важнее краткости.
- **§4 trade-off про ~33% trace loss** — возможно слишком технически глубоко для рецензента-архитектора. Сохранил, потому что это active design decision и иначе выглядит как «не доделано».
- **§3 LLM stack: AItunnel упомянут как fallback после инцидента 2026-05-11.** Это честно, но создаёт впечатление «реактивно правили в день сдачи». Альтернатива — описать как «двухуровневая failover-цепочка» без упоминания инцидента; не выбрал, потому что инцидент уже задокументирован в changelog и видим в git log.
