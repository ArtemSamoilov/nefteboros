# 2026-05-11 — README sync + docs/modules + per-pipeline metrics map

## Задача

К релизу v2.3.5 README отстал от реальности по трём фронтам, доступной документации по подграфам нет, и оценщик из Сбера не имеет «карты, где какие метрики собираются». Финальный pre-deadline sync — закрыть эти пробелы без правок кода.

## Что сделано

### 1. README — три фактологические правки

- **Forecast.** Удалено «SARIMAX + GBR ensemble» — production-метод сменён на regime-conditioned Ornstein-Uhlenbeck (mean-reverting per scenario, [ADR-0024 ou-regime-forecast](../adr/0024-ou-regime-forecast.md)). Stat-models (SARIMAX, GBR, Ensemble, RandomWalk) остаются в репо для backtest infrastructure (regression testing), но не используются на production forecast path. Кратко указано почему: stat-модели на 5y train treat price как unbounded random walk (Var ~ σ²t), дают расходящиеся CI; OU имеет bounded variance — отражает structural property commodities (cost-of-production floor + demand-destruction ceiling).
- **Команда индексации.** `python scripts/index_corpus.py` (не существует в репо) → `python -m scripts.build_index` (реальный скрипт `scripts/build_index.py`).
- **Telegram-бот.** Сняты `Status: WIP` badge и общий `⚠️ В разработке` warning. Telegram-бот реализован (`nefteboros/bot/`, aiogram), но в текущей prod-инсталляции на Timeweb отключён: исходящие подключения к Telegram Bot API блокируются российским провайдером (RKN). Запуск возможен либо вне РФ, либо через VPN/прокси.

### 2. README — три новых раздела

- **«Выбор LLM».** Три провайдера: GigaChat (langchain_gigachat), HydraGPT (langchain_openai через base_url), AITunnel (secondary fallback после билинг-suspend Hydra 2026-05-11). Текущий prod — `kimi-k2p6` через Hydra + `kimi-k2.6` через AItunnel как fallback. GigaChat поддерживается архитектурно (одна переменная env), не активен из-за уже отстроенного tool-calling на kimi. Указано почему не используем OpenAI/Anthropic/Google (доступ из РФ + compliance) и локальные модели (VRAM/setup).
- **«Архитектура модулей».** Таблица со ссылками на `docs/modules/*.md`.
- **«Метрики качества».** Обновлена таблица: per-подграф метрики, скрипт, датасет. Добавлен baseline v2.3.5 (success/cite/struct/refusal). Помечен placeholder на `docs/eval-results-v2.3.5.md` (создаётся отдельной сессией).

### 3. Новый каталог `docs/modules/`

7 файлов, один на пайплайн:

| Файл | Назначение | Метрики |
|---|---|---|
| `analyst_graph.md` | Головной LangGraph (router → forecast → synthesize → validate) | ✅ есть (per-node @observe) |
| `routing.md` | classify_intent (rule-based) + llm_disambiguate | ✅ есть (eval_intent_classifier.py) |
| `rag.md` | BGE-M3 + ChromaDB + rerank | ✅ есть (eval_rag.py + 3 sub-spans) |
| `web_search.md` | Brave API + tier + lang detect | ⚠️ частично (brave_api_call span; **dedicated eval нет**) |
| `forecast.md` | OU regime (production) + statmodels (backtest) | ✅ есть (eval_ou.py + eval_forecast.py) |
| `citation.md` | D6 validator (RAG/web/forecast patterns) | ⚠️ частично (узловой span; **eval_citations.py — placeholder NotImplementedError**) |
| `consolidator.md` | Ouroboros background summarization | ❌ **нет инструментации, нет eval** |

Каждый файл содержит: назначение (1-2 строки), точка входа (`file:line`), входы/выходы, ключевые ADR, метрики (где, какие, в каком файле собираются).

### 4. Новая ADR

[ADR-0025 — README sync + docs/modules structure](../adr/0025-readme-modules-structure.md) — решение завести `docs/modules/` как «карту пайплайнов для оценщика», правила «один файл на пайплайн», правило обязательно отмечать «инструментация: есть/частично/нет».

## Что НЕ делается в этом PR

- Код пайплайнов не правится (out-of-scope; задачи на исправление багов — отдельные PRs).
- `eval_citations.py` остаётся placeholder'ом (отдельная задача, не documentation).
- ADR-0014 / 0015 / 0018 / 0019 не правятся (актуальны).
- ADR-0023 не помечается как deprecated сильнее (уже отмечен как legacy в ADR-0024).

## Найденные при работе несоответствия (для координатора)

1. **Дубликаты номеров ADR:**
   - `0016-embed-retrieve.md` + `0016-forecast-skill.md` (две ADR с одним номером).
   - `0024-observability-langfuse.md` + `0024-ou-regime-forecast.md` (две ADR с одним номером).
2. **Мёртвая ссылка на ADR-0025 в коде**: `nefteboros/graphs/analyst_graph.py:4` ссылается на `ADR-0025 (observability — @observe через wrap при add_node)`, но в репо такого файла нет. Похоже, в комментарии перепутан номер с `0024-observability-langfuse.md`. После мёрджа этого PR номер `0025` будет занят документной ADR (об structure docs/modules), не observability — комментарий в коде стоит поправить отдельно.
3. **`scripts/eval/eval_citations.py` — placeholder** (`raise NotImplementedError`). D6 не имеет offline regression-тестов; в e2e baseline `cite=0.181` — основной gap агента.
4. **`scripts/eval/eval_routing.py` vs `scripts/eval/eval_intent_classifier.py`** — назначение второго не очевидно; возможно legacy / дубликат.
5. **Web search dedicated eval нет** — качество web покрывается только через e2e success rate.
6. **README старый раздел утверждал** «Anti-hallucination валидатор цитат для RAG (web — в backlog)» — на 2026-05-11 действительно RAG-only; web-цитаты проверяются только синтаксически, content fidelity не валидируется. Это перенесено в новый раздел без улучшений (так и есть в коде).
7. **Consolidator (`ouroboros/consolidator.py`) — orphan в observability**: не инструментирован, eval отсутствует. Backlog v2.4 (`changelog/2026-05-11-observability-post-span-flush.md` §«Orphan tool traces из background tasks»).

## Слабые места этой работы

- **Placeholder ссылка** на `docs/eval-results-v2.3.5.md` — этот файл создаёт другая сессия. Если её PR не вмёрджится перед этим, в README будет битый линк до того, как координатор сольёт всё.
- **Eval_routing.py** не вскрыт — я указал что его назначение неясно, но не залез внутрь, чтобы убедиться (out-of-scope: не правлю код, и для определения «дубликат или нет» нужен бы grep сравнения логики).
- **ADR-0025 (этот PR) обходит коллизии 0016 / 0024**, не исправляет их. Переименование существующих ADR — отдельная задача (risk: ломает ссылки в других ADR и changelog'ах).
- **README claim «GigaChat поддерживается архитектурно»** — проверено по ADR-0007 и коду `nefteboros/llm/`, но не запущен end-to-end smoke на prod с `OUROBOROS_MODEL=gigachat::*`. Если оценщик попытается переключить — может всплыть несовместимость на стороне `_resolve_remote_target`. Mitigation: smoke test перед демо.

## Refs

- [ADR-0025](../adr/0025-readme-modules-structure.md) — структура docs/modules.
- [ADR-0024 (OU regime)](../adr/0024-ou-regime-forecast.md) — обоснование SARIMAX → OU.
- [ADR-0024 (observability)](../adr/0024-observability-langfuse.md) — Langfuse + JSON-trace.
- [ADR-0007](../adr/0007-llm-providers.md) — GigaChat + HydraGPT (исходная ADR).
- [changelog 2026-05-11-aitunnel-llm-fallback.md](2026-05-11-aitunnel-llm-fallback.md) — AItunnel fallback.
- [changelog 2026-05-11-observability-post-span-flush.md](2026-05-11-observability-post-span-flush.md) — flush + bundled prod-compat.
