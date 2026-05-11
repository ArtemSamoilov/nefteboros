# 2026-05-11 — README sync + docs/modules + per-pipeline metrics map

## Задача

К релизу v2.3.5 README отстал от реальности по трём фронтам, доступной документации по подграфам нет, и оценщик из Сбера не имеет «карты, где какие метрики собираются». Финальный pre-deadline sync — закрыть эти пробелы без правок кода.

## Что сделано

### 1. README — фактологические правки

- **Forecast.** Удалено «SARIMAX + GBR ensemble» — production-метод сменён на regime-conditioned Ornstein-Uhlenbeck (mean-reverting per scenario, [ADR-0024 ou-regime-forecast](../adr/0024-ou-regime-forecast.md)). Stat-models (SARIMAX, GBR, Ensemble, RandomWalk) остаются в репо для бэктест-инфраструктуры (regression testing), но не используются на production forecast path. Кратко указано почему: статистические модели на 5y train рассматривают цену как unbounded random walk (Var ~ σ²t), дают расходящиеся CI; OU имеет bounded variance — отражает структурное свойство товарных рынков (нижний предел из себестоимости плюс верхний из деструкции спроса).
- **Команда индексации.** `python scripts/index_corpus.py` (не существует в репо) → `python -m scripts.build_index` (реальный скрипт `scripts/build_index.py`).
- **Telegram-бот.** Полностью удалён из README (5+ упоминаний: bullet в «Возможности», строка в «Оставлено», строка в ASCII-tree, упоминание в Docker-секции, env-переменная `TELEGRAM_BOT_TOKEN`, плейсхолдер ссылки на `docs/deploy/production-config.md`). Код в `nefteboros/bot/` остаётся как unused subdir. К сдаваемому web-deploy на Timeweb Telegram отношения не имеет (RKN-блок исходящих к Bot API); упоминание «реализован, но не работает» — шум для рецензента.
- **`Status: WIP` badge** заменён на `Release v2.3.5`.

### 2. README — новые разделы

- **«Выбор LLM».** Три провайдера: GigaChat (langchain_gigachat), Hydra (совместимый с API OpenAI), AITunnel (резервный после биллинг-suspend Hydra 2026-05-11). Текущий prod — `kimi-k2p6` через Hydra + `kimi-k2.6` через AITunnel как резерв. GigaChat поддерживается архитектурно (одна переменная env), не активен из-за уже отстроенного вызова инструментов на kimi. Указано почему не используем OpenAI/Anthropic/Google (доступ из РФ + compliance) и локальные модели (VRAM/setup).
- **«Архитектура модулей».** Таблица со ссылками на `docs/modules/*.md`.
- **«Метрики качества».** Обновлена таблица: per-подграф метрики, скрипт, датасет. Добавлен baseline v2.3.5 (success/cite/struct/refusal). Помечен плейсхолдер на `docs/eval-results-v2.3.5.md`. Явный bullet про **`eval_citations.py` — заглушка**: baseline `cite=0.181` измерен через e2e-eval, выделенный validator offline отсутствует.

### 3. Англицизмы

README + module-docs прочёсаны под стандарт Sber-отчёта. Английские слова оставлены в четырёх случаях: имена продуктов/моделей/библиотек (ChromaDB, LangGraph, Kimi K2, GigaChat, Langfuse, Hydra, AItunnel, Brave, BGE-M3, Streamlit, Docker, Ouroboros, aiogram, Marker, Timeweb), имена в коде (`OUROBOROS_MODEL`, env-переменные, file paths), тех-аббревиатуры с уникальной семантикой (RAG, OU, SARIMAX, GBR, JSON, env, prod, MAPE, CI, vol, API, VPN, RKN, MOEX), устоявшиеся финансовые термины (бэктест, MAPE). Остальное переведено: tool-loop → инструментальный цикл, skill system → система навыков, multi-provider LLM router → маршрутизатор LLM по нескольким провайдерам, web UI → веб-интерфейс, safety/sandbox → безопасность и песочница, tier-1/tier-2 → фильтр уровней, lang detection → определение языка, block-wise summarization → блочная суммаризация, background → фоновый, tool-calling → вызов инструментов, backtest only → только для бэктеста, OpenAI-compatible → совместимый с API OpenAI.

### 4. Новый каталог `docs/modules/`

7 файлов, один на пайплайн:

| Файл | Назначение | Метрики |
|---|---|---|
| `analyst_graph.md` | Головной LangGraph (router → forecast → synthesize → validate) | ✅ есть (per-node @observe) |
| `routing.md` | classify_intent (rule-based) + llm_disambiguate | ✅ есть (eval_intent_classifier.py) |
| `rag.md` | BGE-M3 + ChromaDB + rerank | ✅ есть (eval_rag.py + 3 sub-spans) |
| `web_search.md` | Brave API + уровни + определение языка | ⚠️ частично (brave_api_call span; **dedicated eval нет**) |
| `forecast.md` | OU regime (production) + статистический ансамбль (только для бэктеста) | ✅ есть (eval_ou.py + eval_forecast.py) |
| `citation.md` | D6 validator (RAG/web/forecast patterns) | ⚠️ частично (узловой span; **eval_citations.py — placeholder NotImplementedError**) |
| `consolidator.md` | Ouroboros background summarization | ❌ **нет инструментации, нет eval** |

Каждый файл содержит: назначение (1-2 строки), точка входа (`file:line`), входы/выходы, ключевые ADR, метрики (где, какие, в каком файле собираются).

### 5. Новая ADR

[ADR-0026 — README sync + docs/modules structure](../adr/0026-readme-modules-structure.md) — решение завести `docs/modules/` как «карту пайплайнов для оценщика», правила «один файл на пайплайн», правило обязательно отмечать «инструментация: есть/частично/нет». Номер `0026`, а не `0025`, — потому что в коде уже есть мёртвая ссылка на «ADR-0025 (observability)» (опечатка вместо `0024-observability-langfuse`); занимать `0025` под docs-structure значило бы создавать новый источник недоумения для рецензента.

## Что НЕ делается в этом PR

- Код пайплайнов не правится (out-of-scope; задачи на исправление багов — отдельные PRs).
- `eval_citations.py` остаётся placeholder'ом (отдельная задача, не documentation).
- ADR-0014 / 0015 / 0018 / 0019 / 0023 не правятся (актуальны).
- Опечатка в `nefteboros/graphs/analyst_graph.py:4` («ADR-0025») не правится в этом PR (это код, не doc) — координатор отдельным PR заменит на `0024-observability-langfuse`.

## Backlog для координатора (не действовать)

1. **Дубликаты номеров ADR:**
   - `0016-embed-retrieve.md` + `0016-forecast-skill.md` (две ADR с одним номером).
   - `0024-observability-langfuse.md` + `0024-ou-regime-forecast.md` (две ADR с одним номером).
2. **Опечатка в коде:** `nefteboros/graphs/analyst_graph.py:4` ссылается на «ADR-0025 (observability)»; корректно — `0024-observability-langfuse`.
3. **`scripts/eval/eval_citations.py` — placeholder** (`raise NotImplementedError`). D6 не имеет offline regression-тестов; в e2e baseline `cite=0.181` — основной gap агента.
4. **`scripts/eval/eval_routing.py` vs `scripts/eval/eval_intent_classifier.py`** — возможный дубликат, назначение второго неочевидно по листингу. Требуется внутренний разбор.
5. **Web search dedicated eval нет** — качество web покрывается только через e2e success rate.
6. **Consolidator (`ouroboros/consolidator.py`) — orphan в наблюдаемости**: не инструментирован, eval отсутствует. Backlog v2.4 (`changelog/2026-05-11-observability-post-span-flush.md` §«Orphan tool traces из background tasks»).
7. **GigaChat smoke на prod рекомендуется перед демо** — claim «GigaChat поддерживается архитектурно» проверен по [ADR-0007](../adr/0007-llm-providers.md) и коду `nefteboros/llm/`, но end-to-end smoke на prod с `OUROBOROS_MODEL=gigachat::*` не запущен.

## Слабые места этой работы

- **Placeholder ссылка** на `docs/eval-results-v2.3.5.md` — этот файл создаёт другая сессия. Если её PR не вмёрджится перед этим, в README будет битый линк до того, как координатор сольёт всё.
- **`eval_routing.py`** не вскрыт — указал что назначение неясно, но не залез внутрь (out-of-scope: не правлю код, и для определения «дубликат или нет» нужен бы grep сравнения логики).
- **ADR-0026 обходит коллизии 0016/0024**, не исправляет их. Переименование существующих ADR — отдельная задача (риск: ломает ссылки в других ADR и changelog'ах).
- **Telegram-код в `nefteboros/bot/`** остался без упоминания в README. Если другой разработчик откроет директорию, увидит «непонятную фичу». Принято: убирать рабочий код за день до сдачи — лишний риск.

## Refs

- [ADR-0026](../adr/0026-readme-modules-structure.md) — структура docs/modules.
- [ADR-0024 (OU regime)](../adr/0024-ou-regime-forecast.md) — обоснование SARIMAX → OU.
- [ADR-0024 (observability)](../adr/0024-observability-langfuse.md) — Langfuse + JSON-trace.
- [ADR-0007](../adr/0007-llm-providers.md) — GigaChat + Hydra (исходная ADR).
- [changelog 2026-05-11-aitunnel-llm-fallback.md](2026-05-11-aitunnel-llm-fallback.md) — AItunnel резерв.
- [changelog 2026-05-11-observability-post-span-flush.md](2026-05-11-observability-post-span-flush.md) — flush + bundled prod-compat.
