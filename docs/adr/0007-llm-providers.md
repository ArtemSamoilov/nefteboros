# ADR-0007 — LLM-провайдеры: GigaChat + HydraGPT

- **Дата:** 2026-05-04
- **Статус:** Принято
- **Контекст:** PR #3 `feature/llm-providers`

## Контекст

ТЗ требует возможности использовать любую LLM с обоснованием выбора в README. Доступные нам провайдеры на российском рынке (с учётом санкций и доступа из РФ):

1. **GigaChat (Sber)** — собственная модель Сбера. Lite/Pro/Max/Ultra. OAuth с Минцифры CA. Доступна напрямую.
2. **HydraGPT** — российский OpenAI-совместимый шлюз (`https://hydragpt.ru`) к моделям Cloud.ru / JOI: kimi-k2p6/k2p5, glm-5p1/glm-5, deepseek-v4-pro/v3p2/v3p1, minimax-m2p7, gpt-oss-120b. Работает из РФ без VPN, один токен.
3. **YandexGPT** — другая локальная альтернатива, но не входит в наш список доступных кредов.
4. **OpenAI / Anthropic / Google** — недоступны напрямую из РФ без VPN, использование для prod-агента под Сбер сомнительно с точки зрения compliance.
5. **Локальные** (Llama/Qwen/GLM через llama.cpp) — отвергнуты для тестового задания: высокая инфраструктурная стоимость (>=24 GB VRAM для приличного качества), длинный setup, не показывают «процесс выбора моделей».

## Решение

Поддерживаем **двух провайдеров**: GigaChat (primary) и HydraGPT (через `langchain_openai.ChatOpenAI` с подменённым `base_url`).

В коде:
- `nefteboros/llm/hydra.py` — фабрика `get_hydra_chat_model()`
- `nefteboros/llm/gigachat.py` — фабрика `get_gigachat_chat_model()`
- `nefteboros/llm/router.py` — `get_chat_model(provider, model, profile)` — единая точка получения LangChain-совместимой модели

## Аргументация

### Почему оба провайдера

**Бонусные очки за GigaChat.** Тестовое задание — для позиции в Сбере, под ассистент Грефа. Использование GigaChat демонстрирует знакомство с продуктом компании.

**Покрытие моделей через HydraGPT.** GigaChat — одна семья моделей (Sber). Через HydraGPT мы получаем доступ к 9 разным моделям (Kimi, GLM, DeepSeek, MiniMax, GPT-OSS) — это даёт пространство для **сравнительной оценки** в `scripts/eval/eval_llm.py`. Без HydraGPT не было бы базы для метрик «какая модель лучше для синтеза ответа аналитика».

**Независимость роутинга и синтеза.** Хорошая практика — использовать дешёвую быструю модель для маршрутизации (`classify_intent` в LangGraph) и более мощную для синтеза. С двумя провайдерами это можно настроить через `PRIMARY_LLM_*` и `ROUTING_LLM_*`.

### Почему `langchain_gigachat`

Сбер поддерживает официальный `langchain-gigachat` SDK (LangChain BaseChatModel). Использование готовой обёртки даёт нам:
- Tool calling (нативно в GigaChat-Pro и выше)
- Streaming
- Async
- Совместимость с LangGraph subgraph без перепрошивки

Альтернатива (своя обёртка над `gigachat` SDK) — значит писать tool-calling парсинг, retries, streaming с нуля. Не оправдано за 8 дней.

### Почему `langchain_openai.ChatOpenAI` для HydraGPT

HydraGPT в OpenAI-совместимом режиме принимает `/v1/chat/completions` с полным набором OpenAI-параметров. `ChatOpenAI(base_url=...)` работает прозрачно — это стандартный паттерн для OpenAI-compatible шлюзов.

Бонус: HydraGPT также поддерживает `/v1/messages` (Anthropic-совместимый). Если будут проблемы с tool-calling в OpenAI-формате на каких-то моделях (kimi/glm) — можно переключить отдельные инстансы на `ChatAnthropic(base_url=...)`. В текущем коде не реализовано (одна абстракция проще), но архитектурно открыто.

## Конфигурация

### Env (см. `.env.example`)

**GigaChat:**
- `GIGACHAT_CREDENTIALS` — base64(client_id:client_secret), обязательно
- `GIGACHAT_SCOPE` — `GIGACHAT_API_PERS` (физлицо) | `GIGACHAT_API_CORP` | `GIGACHAT_API_B2B`
- `GIGACHAT_MODEL` — default `GigaChat-Max`
- `GIGACHAT_BASE_URL`, `GIGACHAT_AUTH_URL` — опциональные override (default — sberbank.ru endpoints)
- `GIGACHAT_VERIFY_SSL` — `true` для prod с установленным CA Минцифры; `false` для dev (default)

**HydraGPT:**
- `HYDRA_API_KEY` — формат `hydra_<32hex>`, получить у `@HydraGPTBot` в Telegram
- `HYDRA_BASE_URL` — default `https://hydragpt.ru/v1`
- `HYDRA_DEFAULT_MODEL` — default `kimi-k2p6`

**Router:**
- `PRIMARY_LLM_PROVIDER`, `PRIMARY_LLM_MODEL` — основная модель синтеза (default: `gigachat` / `GigaChat-Max`)
- `ROUTING_LLM_PROVIDER`, `ROUTING_LLM_MODEL` — для classify_intent (default: `hydra` / `glm-5`)

### Использование в коде

```python
from nefteboros.llm import get_chat_model

# Primary (по умолчанию из env)
llm = get_chat_model()
response = llm.invoke("Что говорит ОПЕК о квотах?")

# Per-call override
fast_llm = get_chat_model(provider="hydra", model="glm-5")

# Routing profile
classifier = get_chat_model(profile="routing")
```

## Последствия

**Плюсы:**
- 10 моделей доступны для сравнения (1 GigaChat + 9 HydraGPT)
- Обе обёртки — стандартные LangChain `BaseChatModel`, прозрачны для LangGraph
- Минимум собственного кода (factory-функции, ~50 строк каждая)
- Чистое разделение primary/routing для оптимизации стоимости

**Минусы / риски:**
- HydraGPT — single point of failure для 9 моделей. Если шлюз упадёт — теряем сразу всю секцию сравнения.
- Tool calling в HydraGPT моделях — не все из них поддерживают. Нужно протестировать в `eval_llm.py` и зафиксировать таблицу совместимости.
- GigaChat OAuth токен живёт ~30 минут, обновляется автоматически SDK; в долгих сессиях возможны задержки на refresh.

**Митигации:**
- В `feature/eval-llm` PR — таблица совместимости моделей (tool calling y/n, streaming y/n, structured output y/n).
- Если какая-то модель критически нужна и не поддерживает tools — обернём в текстовый prompt-engineering вариант (LangChain `JsonOutputParser` + few-shot).

## Что НЕ делается в этом PR

- Tool-calling спецификации (общая `bind_tools()` обёртка для адаптеров) — добавится в `feature/langgraph-subgraph`
- Cost / latency tracking — добавится в `feature/eval-llm`
- Anthropic-route HydraGPT — открытая опция, не реализована
- Локальные модели (llama.cpp) — отдельный ADR при необходимости

## Альтернативы рассмотренные

- **Только GigaChat** — потерян пласт «сравнение моделей», нечего показывать в `eval_llm.py`.
- **Свой OpenAI client + httpx** — больше кода, никаких преимуществ перед `langchain-openai`.
- **Yandex GPT** — нет кредов, отложено.

## Ссылки

- HydraGPT: <https://hydragpt.ru>
- GigaChat API: <https://developers.sber.ru/docs/ru/gigachat/api/overview>
- langchain-gigachat: <https://github.com/ai-forever/gigachain>
- ADR-0001: [docs/adr/0001-fork-ouroboros.md](0001-fork-ouroboros.md)
