# nefteboros

> AI-агент «Старший аналитик нефтегазового рынка» на базе [Ouroboros](https://github.com/joi-lab/ouroboros-desktop) (форк) с RAG-пайплайном по отраслевым отчётам, веб-поиском и расчётным модулем прогноза цен Brent.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Status: WIP](https://img.shields.io/badge/status-WIP-orange.svg)](docs/changelog/)

## Что это

Тестовое задание на разработку отраслевого AI-агента. Полное ТЗ — [docs/tz/original.md](docs/tz/original.md).

**Возможности:**
- Отвечает на вопросы по нефтегазовой отрасли (Upstream/Midstream/Downstream, ОПЕК+, ценообразование Brent/WTI/Urals, санкции, спрос/предложение)
- Гибридный поиск: RAG по отчётам OPEC/IEA/EIA + веб-поиск через Brave API с tier-фильтрацией и auto language detection (RU-запрос → RU-источники, EN → EN)
- Прогноз цен Brent на 1/3/6/12 месяцев (ARIMA / SARIMAX, доверительные интервалы)
- Логика приоритизации источников: RAG → web → forecast с явной маркировкой `[Отчёт OPEC MOMR, март 2026]` vs `[Источник: reuters.com, web]` vs `[Forecast: ARIMA, CI 80%]`
- Anti-hallucination валидатор цитат для RAG (web — в backlog)

**Интерфейсы:**
- Web UI (Ouroboros web, в перспективе — Streamlit)
- Telegram-бот (свой, на aiogram)

## Архитектура

Проект — fork [`joi-lab/ouroboros-desktop`](https://github.com/joi-lab/ouroboros-desktop) (MIT). Из upstream'а:
- **Оставлено:** tool-loop, skill system, multi-provider LLM router, web UI, Telegram bridge, safety/sandbox
- **Удалено:** consciousness, reflection, deep_self_review, improvement_backlog, consolidator, marketplace, A2A protocol (всё про самомодификацию — не нужно для отраслевого агента)
- **Добавлено:** доменная логика в `nefteboros/`, skill `neftegaz_analyst/`, LangGraph subgraph для маршрутизации, GigaChat и Cloud.ru адаптеры

Подробно: [docs/architecture.md](docs/architecture.md), [ADR-0001](docs/adr/0001-fork-ouroboros.md).

```
nefteboros/
├── ouroboros/              # ядро (форк, после выпила self-modify)
├── nefteboros/             # доменный код:
│   ├── rag/                #   PDF → BGE-M3 → ChromaDB
│   ├── forecast/           #   ARIMA / SARIMAX с CI
│   ├── search/             #   Brave API + tier-1/tier-2 фильтр + lang detection (ADR-0022)
│   ├── llm/                #   GigaChat, Cloud.ru
│   ├── graphs/             #   LangGraph subgraph
│   ├── prompts/            #   системный промпт аналитика
│   ├── citations/          #   anti-hallucination валидатор (RAG)
│   └── bot/                #   Telegram-бот (aiogram)
├── skills/neftegaz_analyst/  # SKILL.md + plugin.py (Ouroboros plugin)
├── data/                   # PDF корпус (gitignored), metadata, vectorstore
├── datasets/               # эталонные датасеты для оценки
├── metrics/runs/           # результаты прогонов eval
├── scripts/eval/           # скрипты оценки качества подграфов
├── deploy/                 # Dockerfile, docker-compose, server setup
└── docs/
    ├── tz/                 # ТЗ
    ├── adr/                # Architecture Decision Records
    ├── changelog/          # описания PR
    ├── architecture.md     # схема системы (Mermaid)
    ├── experiments/        # дизайн экспериментов и метрики
    └── upstream/           # оригинальные документы Ouroboros
```

## Запуск

> ⚠️ В разработке. Этот раздел будет обновлён по мере реализации компонентов. См. [docs/changelog/](docs/changelog/) для текущего состояния.

### Локально (dev)

```bash
git clone https://github.com/ArtemSamoilov/nefteboros.git
cd nefteboros
cp .env.example .env  # заполни ключи (см. ниже)
pip install -r requirements.txt -r requirements-domain.txt

# индексировать PDF корпус (после загрузки в data/corpus/)
python scripts/index_corpus.py

# запустить web UI
python launcher.py
```

### Docker

```bash
docker compose -f deploy/docker-compose.yml up
```

Поднимет: web UI, Telegram-бот, ChromaDB.

## Конфигурация

Все ключи — в `.env` (пример: [.env.example](.env.example)). Минимально нужны:
- `GIGACHAT_CREDENTIALS` — получить на [developers.sber.ru](https://developers.sber.ru/portal/products/gigachat-api)
- `CLOUDRU_API_KEY` + `CLOUDRU_BASE_URL` — Cloud.ru Foundation Models (для kimi/glm/deepseek)
- `BRAVE_API_KEY` — [brave.com/search/api/](https://brave.com/search/api/)
- `TELEGRAM_BOT_TOKEN` — [@BotFather](https://t.me/BotFather)

## Метрики и оценка качества

Каждый подграф системы измеряется отдельно:

| Подграф | Метрики | Скрипт | Датасет |
|---|---|---|---|
| RAG retriever | hit@k, MRR, recall@k | `scripts/eval/eval_rag.py` | `datasets/rag_qa.jsonl` |
| Routing | accuracy, F1, confusion matrix | `scripts/eval/eval_routing.py` | `datasets/routing.jsonl` |
| Citations | precision, recall, false-attribution rate | `scripts/eval/eval_citations.py` | `datasets/citations_gold.jsonl` |
| Forecast | MAPE, RMSE, coverage 80/95% | `scripts/eval/eval_forecast.py` | `datasets/forecast_history.csv` |
| LLM models | latency, cost, faithfulness | `scripts/eval/eval_llm.py` | `datasets/e2e_dialogues.jsonl` |
| End-to-end | success rate, citation correctness | `scripts/eval/eval_e2e.py` | `datasets/e2e_dialogues.jsonl` |

Дизайн экспериментов: [docs/experiments/design.md](docs/experiments/design.md).

## История изменений

См. [docs/changelog/](docs/changelog/) — отдельный документ на каждый PR с описанием задачи, решения, изменений и тестов.

## Лицензия

MIT (наследуется от Ouroboros). См. [LICENSE](LICENSE) и [NOTICE](NOTICE).
