# 2026-05-04 — LLM providers: GigaChat + HydraGPT (feature/llm-providers)

## Задача

Подключить два LLM-провайдера для нефтегазового аналитика:
- **GigaChat** (Sber) — primary для синтеза ответов; даёт бонусные очки в тестовом задании Сбера
- **HydraGPT** — OpenAI-совместимый шлюз к 9 моделям (kimi/glm/deepseek/minimax/gpt-oss); используется для маршрутизации (быстрая дешёвая модель) и для сравнительной оценки в `scripts/eval/eval_llm.py`

Обе обёртки должны возвращать LangChain `BaseChatModel`-совместимые объекты, чтобы LangGraph subgraph (`nefteboros/graphs/`) мог использовать их прозрачно.

## Контекст

См. [ADR-0007](../adr/0007-llm-providers.md) для подробной аргументации. Краткая логика:
- ТЗ требует «обоснованного выбора LLM» — два провайдера дают пространство для метрик
- Из РФ доступны GigaChat напрямую и HydraGPT (OpenAI-совместимый шлюз), оба без VPN
- LangChain-обёртки уже существуют (`langchain-gigachat`, `langchain-openai`) — не пишем своих

## Что сделано

### Новые файлы

**`nefteboros/llm/hydra.py`** (~50 строк)
- `get_hydra_chat_model(model=None, **kwargs) -> ChatOpenAI`
- Через `langchain_openai.ChatOpenAI(base_url="https://hydragpt.ru/v1", api_key=$HYDRA_API_KEY)`
- Дефолты: `model="kimi-k2p6"`, `temperature=0.2`, `max_tokens=4096`
- Constants: `HYDRA_DEFAULT_BASE_URL`, `HYDRA_DEFAULT_MODEL`

**`nefteboros/llm/gigachat.py`** (~70 строк)
- `get_gigachat_chat_model(model=None, **kwargs) -> langchain_gigachat.GigaChat`
- Lazy import `langchain_gigachat` — понятная ошибка при отсутствии пакета
- Резолвинг env: `GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE`, `GIGACHAT_MODEL`, `GIGACHAT_BASE_URL`, `GIGACHAT_AUTH_URL`, `GIGACHAT_VERIFY_SSL`
- Совместим с форматом `.env` из проекта `anima_backend`

**`nefteboros/llm/router.py`** (~70 строк)
- `get_chat_model(provider=None, model=None, profile=None, **kwargs)`
- `profile="routing"` — отдельный путь под classify_intent (читает `ROUTING_LLM_*`)
- Алиас `hydragpt` → `hydra` для совместимости

**`nefteboros/llm/__init__.py`** (обновлён)
- Реэкспорт `get_chat_model` для удобного импорта `from nefteboros.llm import get_chat_model`

**`docs/adr/0007-llm-providers.md`**
- Контекст и решение
- Аргументация выбора двух провайдеров
- Конфигурация env, примеры использования
- Известные риски и митигации

**`tests/llm/__init__.py`** (пустой) и **`tests/llm/test_router_smoke.py`** (~80 строк)
- Smoke-тесты без сетевых вызовов:
  - `test_unknown_provider_raises` — невалидный provider → ValueError
  - `test_hydragpt_alias_normalizes_to_hydra`
  - `test_hydra_missing_api_key` / `test_gigachat_missing_credentials` — понятная ошибка при отсутствии env
  - `test_hydra_factory_with_explicit_args` / `test_hydra_reads_env`
  - `test_gigachat_factory_constructs` / `test_gigachat_scope_default`
- Все используют `monkeypatch` env, импорт `langchain_gigachat` через `importorskip`

### Изменённые файлы

**`.env.example`**
- Добавлены `GIGACHAT_BASE_URL`, `GIGACHAT_AUTH_URL` (синхронизация с `anima_backend/.env`)
- Удалены `CLOUDRU_*` переменные (заменены на HydraGPT)
- Добавлены `HYDRA_API_KEY`, `HYDRA_BASE_URL`, `HYDRA_DEFAULT_MODEL`
- `PRIMARY_LLM_PROVIDER`: `gigachat | hydra` (было `gigachat | cloudru`)
- `ROUTING_LLM_PROVIDER` дефолт: `hydra` (default routing model: `glm-5`)

**`requirements-domain.txt`**
- `gigachat>=0.1.30` → `langchain-gigachat>=0.3.0` (использует официальный LangChain wrapper)
- Поправлен комментарий к `langchain-openai`: HydraGPT вместо Cloud.ru

## Тесты

`pytest tests/llm/test_router_smoke.py` — 8 smoke-тестов. Сетевые интеграционные тесты — в `scripts/eval/eval_llm.py` (планируется в `feature/eval-llm` PR).

Реальный прогон smoke-тестов в этом PR не сделан (нет venv в репо). На сервере и в Dockerfile они должны проходить из коробки.

## Что ещё НЕ сделано (для следующих PR)

- **Tool-calling** — общая `bind_tools()` обёртка для адаптеров (для LangGraph subgraph). Сейчас `bind_tools` работает напрямую через LangChain — для GigaChat-Pro+ и для HydraGPT-моделей с function calling.
- **Cost / latency tracking** — на каждый вызов писать в `metrics/runs/`. Перенесено в `feature/eval-llm`.
- **Anthropic-route HydraGPT** — `https://hydragpt.ru/v1/messages` доступен, но в коде не реализован (одна обёртка проще).
- **Локальные модели** (llama.cpp) — отдельный ADR при необходимости.

## Файлы

**Добавлены (5):**
- `nefteboros/llm/hydra.py`
- `nefteboros/llm/gigachat.py`
- `nefteboros/llm/router.py`
- `docs/adr/0007-llm-providers.md`
- `tests/llm/__init__.py`, `tests/llm/test_router_smoke.py`

**Изменены (3):**
- `nefteboros/llm/__init__.py` — описание модулей актуализировано, реэкспорт `get_chat_model`
- `.env.example` — переезд `CLOUDRU_*` → `HYDRA_*` + `GIGACHAT_BASE_URL/AUTH_URL`
- `requirements-domain.txt` — `gigachat` → `langchain-gigachat`

## Связанные документы

- ADR-0007: [docs/adr/0007-llm-providers.md](../adr/0007-llm-providers.md)
- ADR-0001: [docs/adr/0001-fork-ouroboros.md](../adr/0001-fork-ouroboros.md)
- LangChain GigaChat: <https://github.com/ai-forever/gigachain>
