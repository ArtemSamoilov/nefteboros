# nefteboros

> AI-агент «Старший аналитик нефтегазового рынка» на базе [Ouroboros](https://github.com/joi-lab/ouroboros-desktop) (форк) с RAG-пайплайном по отраслевым отчётам, веб-поиском и сценарным прогнозом цен Brent/WTI/Urals/ESPO + газа + российской нефтегазовой equity.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/badge/release-v2.3.5-blue.svg)](docs/changelog/)

## Что это

Тестовое задание на разработку отраслевого AI-агента. Полное ТЗ — [docs/tz/original.md](docs/tz/original.md).

**Возможности:**
- Отвечает на вопросы по нефтегазовой отрасли (Upstream/Midstream/Downstream, ОПЕК+, ценообразование Brent/WTI/Urals/ESPO, санкции, спрос/предложение)
- Гибридный поиск: RAG по отчётам OPEC/IEA/EIA + веб-поиск через Brave API с фильтрацией по уровням источников и автоматическим определением языка (RU-запрос → RU-источники, EN → EN)
- Прогноз цен Brent/WTI/Urals/ESPO/HenryHub/TTF + российской energy equity (MOEX-OG, GAZP, NVTK) на 1/3/6/12 месяцев — **regime-conditioned Ornstein-Uhlenbeck (mean-reverting per scenario)**, доверительные интервалы 80/95%, сценарии `bear`/`base`/`bull` под текущий шок 2026-05 (Iran/Hormuz). Обоснование перехода с SARIMAX+GBR ансамбля на OU — [ADR-0024](docs/adr/0024-ou-regime-forecast.md) (кратко: статистические модели рассматривают цену как unbounded random walk и дают расходящиеся CI ±30-40% на 12m; OU даёт ограниченную дисперсию, что отражает структурное свойство товарных рынков — нижний предел из себестоимости плюс верхний из деструкции спроса)
- Приоритизация источников: RAG → веб → прогноз с явной маркировкой `[Отчёт OPEC MOMR, март 2026]` vs `[Источник: reuters.com, web]` vs `[Forecast: ou_regime, scenario=base, CI 80%]`
- Анти-галлюцинационный валидатор цитат для RAG (веб- и forecast-метки — пока только проверка синтаксиса, не сверка содержания; см. [docs/modules/citation.md](docs/modules/citation.md))

**Интерфейс:** веб-интерфейс (Ouroboros web, по умолчанию `http://localhost:7860`).

## Архитектура

Проект — форк [`joi-lab/ouroboros-desktop`](https://github.com/joi-lab/ouroboros-desktop) (MIT). Из апстрима:
- **Оставлено:** инструментальный цикл, система навыков, маршрутизатор LLM по нескольким провайдерам, веб-интерфейс, безопасность и песочница, фоновый консолидатор истории чата.
- **Удалено:** всё про самомодификацию (самосознание, рефлексия, глубокий самоанализ, бэклог улучшений, marketplace, протокол A2A) — для отраслевого агента не нужно.
- **Добавлено:** доменная логика в `nefteboros/`, навык `neftegaz_analyst/`, LangGraph subgraph для маршрутизации, наблюдаемость (Langfuse + JSON-trace), резервный провайдер AITunnel.

Подробно: [docs/architecture.md](docs/architecture.md), [ADR-0001](docs/adr/0001-fork-ouroboros.md).

```
nefteboros/
├── ouroboros/              # ядро (форк, после выпила self-modify)
├── nefteboros/             # доменный код:
│   ├── rag/                #   PDF → BGE-M3 → ChromaDB + rerank
│   ├── forecast/           #   OU regime (production) + SARIMAX/GBR (только для бэктеста)
│   ├── search/             #   Brave API + фильтр уровней + определение языка
│   ├── llm/                #   GigaChat, Hydra (совместимый с API OpenAI), AITunnel
│   ├── graphs/             #   LangGraph analyst subgraph
│   ├── prompts/            #   системный промпт аналитика
│   ├── citations/          #   анти-галлюцинационный валидатор + regex-паттерны
│   └── observability/      #   Langfuse + JSON-trace + Ouroboros patches
├── skills/neftegaz_analyst/  # SKILL.md + plugin.py (Ouroboros plugin)
├── data/                   # PDF корпус (gitignored), metadata, vectorstore
├── datasets/               # эталонные датасеты для оценки
├── metrics/runs/           # результаты прогонов eval + JSONL traces
├── scripts/eval/           # скрипты оценки качества подграфов
├── deploy/                 # Dockerfile, docker-compose, server setup
└── docs/
    ├── tz/                 # ТЗ
    ├── adr/                # Architecture Decision Records
    ├── changelog/          # описания PR
    ├── modules/            # справка по пайплайнам (см. ниже)
    ├── architecture.md     # схема системы (Mermaid)
    ├── experiments/        # дизайн экспериментов и метрики
    └── upstream/           # оригинальные документы Ouroboros
```

## Архитектура модулей

Подробное описание каждого пайплайна (точка входа, входы/выходы, ADR, метрики) — в [docs/modules/](docs/modules/):

| Модуль | Что делает | Документ |
|---|---|---|
| Analyst Graph | Головной LangGraph: маршрутизация → прогноз → синтез → проверка цитат | [docs/modules/analyst_graph.md](docs/modules/analyst_graph.md) |
| Routing | classify_intent (по правилам) + llm_disambiguate (LLM на `no_keyword_match`) | [docs/modules/routing.md](docs/modules/routing.md) |
| RAG | BGE-M3 embeddings → ChromaDB dense → фильтр по теме → rerank | [docs/modules/rag.md](docs/modules/rag.md) |
| Web Search | Brave API + классификация уровней + определение языка | [docs/modules/web_search.md](docs/modules/web_search.md) |
| Forecast | OU regime (production), сценарии bear/base/bull, CI 80/95 | [docs/modules/forecast.md](docs/modules/forecast.md) |
| Citation | Анти-галлюцинационный валидатор (D6, RAG/web/forecast паттерны) | [docs/modules/citation.md](docs/modules/citation.md) |
| Consolidator | Блочная суммаризация истории чата (фоновый процесс Ouroboros) | [docs/modules/consolidator.md](docs/modules/consolidator.md) |

## Запуск

### Локально (dev)

```bash
git clone https://github.com/ArtemSamoilov/nefteboros.git
cd nefteboros
cp .env.example .env  # заполнить ключи (см. ниже)

# Python 3.12 (на 3.14 ломается langchain-gigachat):
uv venv -p 3.12
source .venv/bin/activate
uv pip install -e . -r requirements-domain.txt

# Индексация PDF-корпуса (после загрузки в data/corpus/):
python -m scripts.build_index

# Запуск веб-интерфейса:
python launcher.py
```

### Docker (prod-like)

```bash
docker compose -f deploy/docker-compose.yml up
```

Поднимет веб-интерфейс и ChromaDB. Готовые multi-arch образы публикуются в GHCR на каждый тег `vX.Y.Z`; pull: `docker pull ghcr.io/artemsamoilov/nefteboros:v2.3.5`.

## Конфигурация

Все ключи — в `.env` (пример: [.env.example](.env.example)). Минимально нужны:
- `OUROBOROS_MODEL` — основная LLM (по умолчанию `openai-compatible::kimi-k2p6` через Hydra; альтернативно `gigachat::GigaChat-Max` или `aitunnel::kimi-k2.6`)
- `OUROBOROS_MODEL_FALLBACK` — резервная модель при отказе основной (по умолчанию `aitunnel::kimi-k2.6`)
- `HYDRA_API_KEY` + `HYDRA_BASE_URL` — для основной через Hydra ([hydragpt.ru](https://hydragpt.ru))
- `AITUNNEL_API_KEY` + `AITUNNEL_BASE_URL` — резервный канал ([api.aitunnel.ru](https://api.aitunnel.ru/v1))
- `GIGACHAT_CREDENTIALS` — опционально, для GigaChat ([developers.sber.ru](https://developers.sber.ru/portal/products/gigachat-api))
- `BRAVE_API_KEY` — [brave.com/search/api/](https://brave.com/search/api/)
- `LANGFUSE_*` — опционально, для наблюдаемости (см. [ADR-0024 наблюдаемость](docs/adr/0024-observability-langfuse.md))

## Выбор LLM

ТЗ требует обоснования выбора моделей. Архитектура подключаемая через LangChain `BaseChatModel`, поддерживаются три провайдера:

1. **GigaChat (Сбер)** — `langchain_gigachat`. Изначально основная модель в ADR ([ADR-0007](docs/adr/0007-llm-providers.md)). Бонус: знакомство с продуктом Сбера для оценщика, RU-данные on-prem, OAuth с CA Минцифры, нативный вызов инструментов в Pro+.
2. **Hydra (kimi-k2p6, glm-5, deepseek и др.)** — `langchain_openai.ChatOpenAI(base_url=...)` через шлюз, совместимый с API OpenAI. Покрывает 9 моделей через одного провайдера; используется для сравнительной оценки и как основная в текущей prod-конфигурации (`OUROBOROS_MODEL=openai-compatible::kimi-k2p6` — kimi-k2p6 показала лучшее покрытие вызова инструментов).
3. **AITunnel** — резервный провайдер с теми же моделями (kimi-k2.6) через российский прокси, совместимый с API OpenAI. Добавлен 2026-05-11 после того, как Hydra (под капотом Fireworks.ai) была suspended за биллинг; без рабочего резерва демо невозможно. См. [changelog 2026-05-11-aitunnel-llm-fallback.md](docs/changelog/2026-05-11-aitunnel-llm-fallback.md).

**Текущий prod (Timeweb VDS, 2026-05-11):** `OUROBOROS_MODEL=openai-compatible::kimi-k2p6`, `OUROBOROS_MODEL_FALLBACK=aitunnel::kimi-k2.6`. GigaChat поддерживается архитектурно (переключается одной переменной env), не активен в prod из-за уже отстроенного вызова инструментов на kimi.

**Что мы НЕ используем и почему:** OpenAI / Anthropic / Google — недоступны напрямую из РФ без VPN, для prod-агента под Сбер сомнительно с точки зрения compliance. Локальные модели (Llama/Qwen) — отвергнуты для тестового задания (≥24 GB VRAM, длинный setup, не показывают «процесс выбора моделей»).

## Метрики качества

Каждый подграф системы измеряется отдельно. Подробности по каждому пайплайну — в [docs/modules/](docs/modules/).

| Подграф | Метрики | Скрипт | Датасет |
|---|---|---|---|
| RAG retriever | chunk_hit@k, source_hit@k, MRR; срез по lang/block | `scripts/eval/eval_rag.py` | `datasets/rag_qa.jsonl` |
| Routing / Intent | type_accuracy, assets_jaccard, horizon_match, P/R/F1 | `scripts/eval/eval_intent_classifier.py` | `datasets/intent_classifier.jsonl` |
| Citations | precision, recall, false_attribution_rate | `scripts/eval/eval_citations.py` ⚠️ заглушка | `datasets/citations_gold.jsonl` |
| Forecast (OU production) | MAPE, Bias, Coverage 80%/95%, per-regime | `scripts/eval/eval_ou.py` | walk-forward 5y |
| Forecast (статистический ансамбль baseline) | MAPE, RMSE, MASE vs RW, directional accuracy | `scripts/eval/eval_forecast.py` | walk-forward |
| Web search | (нет выделенного скрипта — косвенно через e2e) | — | — |
| End-to-end | success rate, citation correctness, structure, refusal correctness | `scripts/eval/eval_e2e.py` | `datasets/e2e_dialogues.jsonl` |

**Релиз v2.3.5, baseline (100 диалогов, реальный прогон):** success=0.568, cite=0.181, struct=0.528, refusal=0.947. Полный отчёт по метрикам — [docs/eval-results-v2.3.5.md](docs/eval-results-v2.3.5.md) *(публикуется отдельной сессией, плейсхолдер)*.

**Известное ограничение:** оценщик цитирования (`scripts/eval/eval_citations.py`) — заглушка (`raise NotImplementedError`), регрессионный тест offline отсутствует. Baseline `cite=0.181` измерен через e2e-eval, не через выделенный citations validator. Полная реализация — в плане v2.4 (Track D6).

Дизайн экспериментов: [docs/experiments/design.md](docs/experiments/design.md). Наблюдаемость (Langfuse + JSON-trace): [ADR-0024 (observability)](docs/adr/0024-observability-langfuse.md).

## История изменений

См. [docs/changelog/](docs/changelog/) — отдельный документ на каждый PR с описанием задачи, решения, изменений и тестов. Архитектурные решения: [docs/adr/](docs/adr/).

Текущая версия — `v2.3.5`. Тэги собираются native multi-arch в GHCR (см. [docs/changelog/2026-05-11-*.md](docs/changelog/)).

## Лицензия

MIT (наследуется от Ouroboros). См. [LICENSE](LICENSE) и [NOTICE](NOTICE).
