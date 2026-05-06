# ADR-0015 — LLM-disambiguate узел через GigaChat-2-Max

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/llm-disambiguate` — расширение analyst graph (см. ADR-0014)
- **Связано:** ADR-0014 (LangGraph subgraph + rule-based classify_intent), ADR-0013 §«Constraints for SKILL.md» (5 правил disambiguation), ADR-0007 (LLM-провайдеры — GigaChat основной)

## Контекст и проблема

PR `feature/langgraph-subgraph` зафиксировал rule-based `classify_intent` как первый узел графа: regex-keyword matching по правилам #1, #3, #5 из ADR-0013. Покрытие — 53 unit-теста на типовые формулировки.

Признанная слабость подхода: **rule-based падает на формулировках вне keyword-набора**. Примеры, которые сейчас попадают в `out_of_scope` с `matched_rule="no_keyword_match"` (deficit, известный из обсуждения):

- «прогноз чёрного золота для российского ТЭК» — нет слов «нефт*», но смысл прозрачен.
- «Сахалин-Light на квартал» — physical proxy для ESPO, наш registry его не содержит.
- «сколько энергоносители принесут в казну» — РФ-контекст без слов «Минфин/бюджет/НДПИ».
- «Bonny Light 6 мес» — light sweet, ближайший proxy в нашем покрытии — Brent.

Артём (assignee аналитика-ассистента для Сбера, см. project memory): «в rule-based regex я не верю; LLM с хорошим промптом решает эту задачу намного лучше». Заодно — карьерная важность демонстрации **GigaChat в реальной задаче** (Sber LLM stack, статус и demo для G. Грефа).

Принято: добавить **LLM-узел только для случая `no_keyword_match`**. Refusal'ы из `rule_5_russian_gas` и `rule_3_horizon` остаются deterministic — не идут в LLM (экономия токенов на очевидных рефуз-сценариях, которые rule-based ловит надёжно).

## Решение

В analyst graph добавляется узел `llm_disambiguate`, вызываемый conditional edge'ом **только** когда `state.intent.matched_rule == "no_keyword_match"`. Узел дёргает GigaChat-2-Max со structured-JSON output, перезаписывает `state.intent` LLM-классификацией. Дальше тот же `_route_after_classify` решает forecast vs synthesize.

Граф после изменения:

```
classify_intent → conditional →
   ├─ rule_5_russian_gas / rule_3_horizon (refusal)        → synthesize
   ├─ rule_1_* (forecast intents)                          → forecast_call → synthesize
   └─ no_keyword_match (rule-based не справился)           → llm_disambiguate →
                                                              conditional →
                                                              ├─ refusal → synthesize
                                                              └─ forecast → forecast_call → synthesize
synthesize → validate_citations → END
```

## Что в этом PR

```
nefteboros/graphs/nodes/
└── llm_disambiguate.py               # NEW — узел графа: вызов GigaChat-2-Max через
                                      #        существующий nefteboros.llm.gigachat
                                      #        + structured output + graceful fallback

nefteboros/prompts/
└── disambiguate_intent.md            # NEW — промпт для GigaChat (5 правил + few-shot
                                      #        + JSON schema)

nefteboros/graphs/analyst_graph.py    # ИЗМЕНЁН — _route_after_classify_initial разводит
                                      #        no_keyword_match → llm_disambiguate, новый
                                      #        узел подключён, edges переписаны

tests/
├── test_llm_disambiguate.py          # NEW — 7 тестов (mock GigaChat через monkeypatch
                                      #        на nefteboros.llm.gigachat.get_gigachat_chat_model)
└── test_graph_smoke.py               # РАСШИРЕН — кейс «прогноз чёрного золота» проходит
                                      #        через llm_disambiguate → forecast_call

docs/adr/0015-llm-disambiguate.md     # этот документ
docs/changelog/2026-05-07-llm-disambiguate.md
```

`nefteboros/llm/gigachat.py` и `router.py` уже существуют (созданы в более ранних PR'ах
проекта согласно ADR-0007) и **не правятся** в этом PR — мы их потребитель.
`langchain-gigachat>=0.3.0` уже в `requirements-domain.txt:12`. Новых зависимостей нет.

## Аргументация — главные неочевидные решения

### Почему через существующий `nefteboros.llm.gigachat`, а не свой httpx-клиент

В проекте уже зафиксирован LLM-stack через `langchain-gigachat` (ADR-0007): фабрика
`get_gigachat_chat_model()` возвращает `BaseChatModel`, поддерживающий `.with_structured_output(PydanticClass)` — именно то, что нужно для classify в типизированный `Intent`. Дублировать прямым httpx-клиентом (как в anima_backend) — это нарушать уже принятое архитектурное решение проекта и удваивать поверхность интеграции с GigaChat.

Минусы переиспользования (которые мы принимаем):
- `langchain-gigachat` тащит lang-chain core (~5 MB). Уже в стеке для других узлов (synthesize),
  не добавляет новой нагрузки.
- Менее прозрачный transport — для дебага HTTP-обмена нужно лезть в langchain debug logs.
  Для PR — приемлемо; если упрёмся — переключимся на httpx как в anima.

Плюсы:
- Один способ интеграции с GigaChat в проекте (DRY).
- `.with_structured_output(_LLMIntent)` — один вызов, валидация автоматом.
- Tests мокают на уровне `get_gigachat_chat_model` — один patch, не дюжина httpx-helpers.

### Почему `GigaChat-2-Max`, а не `GigaChat`/`Lite`

- Аргумент Артёма: «Max лучше держит structured output». Подтверждается практикой — у Lite-моделей JSON-mode чаще ломается на не-тривиальных схемах (вложенные списки, conditional fields).
- Стоимость: на demo/тестовое не критично; на масштабе production — стоит вернуться, заменить на Lite если качество позволяет.
- Default берётся из env `GIGACHAT_MODEL` (как в anima_backend) — оператор может переопределить без правки кода.

### Почему LLM **только** на `no_keyword_match`, а не везде

- Refusal'ы `rule_5_russian_gas` (запрос про прямые цены РФ-газа) и `rule_3_horizon` (1d/24m) — **deterministic** и **корректные**. Прогонять их через LLM = риск что LLM «улучшит» refusal до «вот вам приближение» и потеряет structural-redirect к RAG. Это контролируемый bad-pattern.
- Forecast intents (`rule_1_*`) — keyword-overhead уже выполнили работу: LLM ничего не добавит, добавит latency и cost.
- `no_keyword_match` = единственное место, где rule-based **сознательно говорит «не знаю»**. Здесь LLM создаёт ценность — пытается понять то, что regex'ы не достали.

Это hybrid disambiguation: deterministic fast path + LLM на остаток. Как в production-системах с regex-prefilter на NLP-моделях — сначала «дешёвые» правила, потом «дорогая» модель.

### Структура prompt'а — short, schema-explicit, examples

Шаблон `nefteboros/prompts/disambiguate_intent.md`:
1. **Кратко**: 4 типа intent + что они значат.
2. **Правила**: 5 правил ADR-0013 в одной фразе каждое (LLM их применяет).
3. **Список валидных активов** генерируется runtime из `ASSET_REGISTRY` — single source of truth.
4. **3 примера** (few-shot): классическая формулировка → JSON. Покрывают forecast_simple, forecast_with_context, out_of_scope.
5. **Schema response**: явный JSON-skeleton с типизированными полями.

В системном промпте (передаётся отдельно): «Возвращай ТОЛЬКО валидный JSON. Без markdown, без префикса, без объяснений вне JSON».

### Fallback при error — оставляем rule-based intent

Узел `llm_disambiguate` **резильентный**:
- `ImportError` (httpx сломан / nefteboros.llm.gigachat не импортируется) → `state.intent` остаётся как был (rule-based out_of_scope), `matched_rule` помечается `llm_unavailable`.
- HTTP-ошибка GigaChat / timeout 3x / parse-fail → то же, `matched_rule="llm_disambiguate_failed"`.
- LLM вернул валидный JSON, но не парсится в `_LLMIntent` (выдумал тип) → retry один раз с error-feedback в next prompt; при повторном fail — fallback.

Цель: **граф не падает**. Если LLM-disambiguate не может — пользователь получает rule-based out_of_scope-ответ с честным объяснением. Это лучше, чем `500 Internal Server Error`.

### Почему отдельный узел, а не fold в classify_intent

- **Testability**: rule-based classify_intent остаётся чистой функцией без I/O — 53 unit-теста не флакуют от network.
- **Изолированный обработчик ошибок**: LLM-вызов имеет свою retry-логику, свою token-cache. В classify_intent его засовывать = смешивать concerns.
- **Возможность отключить** через config (`OUROBOROS_LLM_DISAMBIGUATE_ENABLED=false`) — узел просто становится no-op'ом, граф работает без LLM. Это полезно для CI / offline-разработки.
- **Единый routing**: новый conditional edge добавляется к graph wiring'у explicit, видно в коде.

## Последствия

**Плюсы:**
- Закрывает deficit rule-based: нестандартные формулировки теперь дисамбигуируются. Артём не придёт и не скажет «вот тут regex не сработал — а должен».
- Демонстрация GigaChat в реальной задаче — карьерно ценно для проекта-в-Сбер. Не «GigaChat везде» (что было бы overkill), а «GigaChat там, где он реально решает».
- Graceful degradation: если GigaChat unavailable (env not set / network / token rotation) — graph всё равно отвечает rule-based out_of_scope. Не блокер.
- Fast path сохраняется: 80% запросов не идут в LLM.
- Архитектурно расширяемо: тот же паттерн (LLM-fallback после rule-based prefilter) применяется потом и для `feature/rag-integration` и `feature/web-integration`.

**Минусы / риски:**
- Дополнительный latency на `no_keyword_match` запросах (~500ms-1s GigaChat round-trip).
- Дополнительная зависимость от Сберовской инфраструктуры (auth-server, base-url). Это уже было через ouroboros.llm для synthesize, но теперь и для classify.
- Тесты с monkeypatch'ем GigaChat client'а — золотая середина: покрывают логику узла, но не интеграцию с реальным GigaChat. Real-LLM smoke остаётся manual / на сервере.
- Hardcoded `verify=False` для самоподписанных сертификатов Сбера (как в anima). Security trade-off, но это требование Сберовской CA в рамках их корпоративной сети.

**Митигации:**
- Token-кеш предотвращает auth-flood'ы (один OAuth2 round-trip на ~30 мин).
- 3 retry на timeout + circuit breaker через fallback на rule-based.
- Env-флаг `OUROBOROS_LLM_DISAMBIGUATE_ENABLED=false` для отключения (опционально, в этом PR не реализуем — узел сам fail'ится в graceful, что эквивалентно).
- В changelog подчёркнуто: GigaChat creds должны быть в `.env` сервера; локальный dev обходится monkeypatch'ами в тестах.

## Что НЕ в этом PR (явно)

- **`OUROBOROS_LLM_DISAMBIGUATE_ENABLED`-флаг** для явного выключения. Не реализуем — graceful fallback на error эквивалентен.
- **Структурированный inputs/outputs** через `function_call` (вместо `response_format: json_object`). Текущая реализация — JSON-mode. Для GigaChat-2-Max этого достаточно; если в production упадёт стабильность — переключим.
- **Расширение `classify_intent` rule'ами** под формулировки, которые часто видим в LLM-output (например, новые keyword'ы для «тяжёлая нефть» = «heavy_sour»). Это адаптация на real telemetry — отдельный PR `feature/intent-rules-tuning`.
- **Real-LLM golden-eval датасет** — фиксированный набор 30+ формулировок для нерегулярных запросов с ожидаемыми intent'ами. Отдельный PR (если время в дедлайне).
- **Метрики latency / token usage** в state.metadata — узел сейчас просто перезаписывает intent. Метрики через логи (`logger.info`) — telemetry-grade observability отложена.
- **Multi-language disambiguate** — текущий промпт RU+EN, но без отдельного language-detection. Если запросы на других языках — тоже отдельный feature.
- **Правка `_route_after_classify_initial` для определения `current_data` / `report_topic`-intent'ов** — эти типы появятся в `feature/rag-integration` / `feature/web-integration` со своими conditional edges.

## Альтернативы рассмотренные

- **Заменить rule-based classify_intent целиком на LLM** (вариант A из обсуждения): отвергнуто — теряем 53 deterministic теста, увеличиваем cost на каждый запрос, теряем fast-path. Артём согласился на hybrid после обсуждения.
- **Hybrid внутри classify_intent (rule-first, LLM-fallback в одной функции)** (вариант B): отвергнуто — смешивает sync rule-based и async LLM-call в одной функции, ход мысли в коде менее очевиден. Отдельный узел чище.
- **Прямой httpx-клиент (как в anima_backend)**: отвергнуто в пользу существующего `nefteboros/llm/gigachat.py` через `langchain-gigachat` — это уже решение ADR-0007 для проекта; дублирование путей интеграции = технический долг. anima_backend паттерн полезен как reference (token-кеш, retry, `profanity_check=False`), но `langchain-gigachat` обёртка их даёт «из коробки».
- **`GigaChat` (Lite) или `GigaChat-Pro`**: отвергнуто — Артём подтвердил Max лучше держит structured output на нерегулярных формулировках. На demo cost не критичен.
- **Вызывать LLM на ВСЕХ refusal'ах для генерации более «человеческих» отказов**: отвергнуто — refusal-text уже составлен под конкретное правило (rule #5 ссылка на TTF/GAZP/RAG; rule #3 ссылка на сценарии RAG). LLM может «улучшить» и потерять эти structural-pointers — это и есть smaller LLM-pitfall.

## Ссылки

- [docs/adr/0014-langgraph-subgraph.md](0014-langgraph-subgraph.md) — minimal-graph baseline (rule-based classify)
- [docs/adr/0013-hybrid-forecasting.md](0013-hybrid-forecasting.md) §«Constraints for SKILL.md» — 5 правил, теперь и LLM их применяет
- [docs/adr/0007-llm-providers.md](0007-llm-providers.md) — GigaChat как primary провайдер
- `app/classifier/gigachat_client.py` (anima_backend) — reference паттерн httpx-клиента, retry и token-cache
- ТЗ §2.4 [docs/tz/original.md](../tz/original.md) — приоритизация источников
