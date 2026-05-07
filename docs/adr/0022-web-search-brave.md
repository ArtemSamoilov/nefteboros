# ADR-0022 — Web search через Brave API как третий tool в neftegaz_analyst skill

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/web-search-integration`
- **Связано:** ADR-0001 (форк Ouroboros), ADR-0014 (LangGraph subgraph), ADR-0016 (forecast-skill, single-tool), ADR-0018 (rag_search, multi-tool architecture), ADR-0019 (system prompt analyst)

## Контекст

ТЗ §2.3/§2.4 требует «инструмент веб-поиска для актуальных данных (свежие новости, текущие котировки, заявления регуляторов)» с фильтрацией жёлтой прессы и явной приоритизацией: RAG → web → forecast. После merge ADR-0018 в skill `neftegaz_analyst` зарегистрированы два tool'а — `analyst_query` (forecast/synthesis) и `rag_search` (802 чанка корпуса). Web-канал отсутствует: `nefteboros/search/__init__.py` пуст, в `prompts/SYSTEM.md` явно зафиксирован TODO «`web_search` пока не зарегистрирован».

Параллельно в форке остаётся **upstream `web_search`** в [`ouroboros/tools/search.py`](../../ouroboros/tools/search.py) — обёртка над **OpenAI Responses API** (timeout 540 сек, нужен `OPENAI_API_KEY` без `OPENAI_BASE_URL`). У нас официального OPENAI ключа нет (LLM-стек — GigaChat + Cloud.ru-compat, см. ADR-0007), upstream-tool на любом вызове возвращает explicit error, но остаётся в каталоге tools и шумит в tool selection LLM.

## Решение — два связанных шага

### 1. Удалить upstream `web_search`

В рамках этого PR полностью убираем upstream-tool из форка:
- `ouroboros/tools/search.py` — удалён;
- регистрация в `ouroboros/tools/registry.py` (`CORE_TOOL_NAMES`, `_FROZEN_TOOL_MODULES`) — вычищена;
- `ouroboros/safety.py` (TOOL_POLICY) — вычищен;
- `ouroboros/tool_capabilities.py` (capability list + `READ_ONLY_PARALLEL_TOOLS`) — вычищен;
- тесты `tests/test_search_tool.py` и `tests/test_web_search_streaming.py` — удалены;
- упоминания в `tests/test_smoke.py`, `tests/test_contracts.py`, `tests/test_chat_logs_ui.py` — обновлены.

Это согласовано с ADR-0001 (выпил self-modify) — мы и так удаляем из upstream'а функциональность, не релевантную отраслевому агенту. У OpenAI Responses web-search два недостатка для нашего use-case: (а) требует OpenAI ключа, которого у нас нет, (б) даёт «answer» (LLM-сжатый ответ), а не структурированный список с `hostname/url/snippet/published` — без структуры мы не можем применить tier-фильтр и не можем поддержать формат маркировки `[Источник: <hostname>, web]` из ТЗ §2.4.

### 2. Зарегистрировать собственный `web_search` через Brave API

Третий tool в `skills/neftegaz_analyst/plugin.py` (multi-tool архитектура из ADR-0018):

```python
api.register_tool(
    "web_search",
    _tool_web_search,
    description=_WEB_TOOL_DESCRIPTION,
    schema=_WEB_TOOL_SCHEMA,
    timeout_sec=20,
)
```

Lazy import `nefteboros.search.WebSearcher`, JSON-payload, error-resilience — паттерн `analyst_query`/`rag_search`.

## Дизайн доменного модуля `nefteboros/search/`

| Файл | Ответственность |
|---|---|
| `models.py` | `SearchHit` dataclass — минимальный shape для LLM-цитирования. |
| `lang.py` | `detect_lang(query) → "ru"\|"en"` по доле кириллицы (порог 0.3); `brave_params_for_lang(lang) → {search_lang, country, ui_lang}`. |
| `tiers.py` | TIER1/TIER2/BLACKLIST hostsets с ENV-override (`NEFTEBOROS_WEB_TIER1_HOSTS=...`); `classify(host)`, `is_blacklisted(host)`. |
| `cache.py` | Самодельный `TTLCache` (~50 строк, без cachetools-зависимости), TTL=1ч, ключ — кортеж. |
| `brave.py` | Sync `BraveClient` через `httpx`. Endpoint `https://api.search.brave.com/res/v1/web/search`, header `X-Subscription-Token`. 1 retry на 429/5xx с exponential backoff, общий timeout 10 сек. |
| `__init__.py` | `WebSearcher` фасад: lang detection → cache lookup → BraveClient → blacklist filter → tier filter → top-k. |

### Почему Brave

ТЗ §2.3 перечисляет «Tavily, SerpAPI, DuckDuckGo, Google Custom Search или аналог». Сравнение для нашего use-case:

| Провайдер | Free tier | Структурированный hostname | Freshness | Russian sources |
|---|---|---|---|---|
| **Brave** | 1 RPS, 2000/мес | да (`meta_url.hostname`) | да (`pd/pw/pm/py`) | приличное покрытие RU-tier1 |
| Tavily | 1000/мес, AI-summaries | да | да | слабее RU |
| SerpAPI | 100/мес | да | да | приличное |
| Google CSE | 100/день | требует custom engine | ограничено | ок |
| DuckDuckGo | API закрыт, scraping ToS-серый | нет | нет | плохо |

Brave даёт оптимум `free tier × качество × freshness × RU-coverage`. `BRAVE_API_KEY` уже зарезервирован в [`.env.example:39`](../../.env.example) и был выбран направлением до этого PR.

### Lang detection — почему по кириллице, а не langdetect

Запрос пользователя на русском должен ловить RU-источники (Vedomosti/Kommersant/RBC), на английском — EN (Reuters/Bloomberg/FT). Без явного `search_lang/country` параметра Brave дефолтит на EN — RU-tier1 теряется.

Подходы рассмотренные:
- **`langdetect`** (~5 МБ deps, Naive Bayes на n-граммах) — шумит на коротких user-query'ях («OPEC квоты» классифицирует то ru, то sl).
- **`fasttext`** (~120 МБ модель) — overkill для бинарной классификации.
- **Доля кириллических букв** (порог 0.3) — детерминирован, без зависимостей, корректен на смешанных query вроде «Новатэк LNG strategy» (≥30% кириллицы → RU).

Выбран третий вариант. Тест `test_search_lang.py` фиксирует поведение на 9+ примерах.

### Tier-фильтрация — где и как

ТЗ §2.3 требует фильтрацию жёлтой прессы. Три уровня в `tiers.py`:

- **TIER1** (verified business/industry): Reuters / Bloomberg / FT / WSJ / Argus / S&P Platts / Wood Mackenzie / Energy Intel / Rystad / IEA / OPEC / EIA + RU-tier1: РБК / Ведомости / Коммерсант / Интерфакс / ТАСС.
- **TIER2** (general business/energy): CNBC / Forbes / RG.ru / Expert / Neftegaz.ru / Energyland.
- **BLACKLIST** (всегда отбрасывается): Reddit / Quora / соцсети / Dzen / LiveJournal / Pikabu / Medium.

Фильтр применяется **на уровне `WebSearcher`**, не Brave-клиента — клиент возвращает все результаты с tier-меткой, фасад фильтрует. Это даёт два преимущества:
1. Tool-параметр `tier="all"|"tier1"` управляет жёсткостью без перезапроса Brave.
2. Кэш не зависит от tier — один Brave-запрос обслуживает оба режима.

Полное переопределение через ENV (`NEFTEBOROS_WEB_TIER1_HOSTS=...`) для prod-гибкости без commit'а кода. Дополнение к defaults — overengineering для MVP.

## Tool spec — критичные моменты

### `_WEB_TOOL_DESCRIPTION`

~900 символов (в пределах OpenAI/Anthropic лимитов). Содержит:
- Когда вызывать (spot-цены, свежие новости, заявления регуляторов).
- Детерминированный язык-routing (RU-запрос → RU-источники).
- Когда НЕ вызывать (documentary факты → `rag_search`; прогнозы → `analyst_query`).
- Явный приоритет ТЗ §2.4 — «сначала пробуй rag_search, если в корпусе нет — тогда web_search».
- Формат возврата + подсказка по citation-стилю `[Источник: <hostname>, web]`.

Это **главное** место, где агент-LLM получает инструкции (`SKILL.md` body LLM не видит — см. ADR-0014 §«Большая бомба I»).

### Schema

```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string"},
    "freshness": {"type": "string", "enum": ["pd", "pw", "pm", "py"]},
    "k": {"type": "integer", "minimum": 1, "maximum": 10},
    "tier": {"type": "string", "enum": ["all", "tier1"]}
  },
  "required": ["query"]
}
```

`freshness` напрямую соответствует Brave-нотации (past-day/week/month/year). `k` ≤ 10 — защита от 10 × 500 chars = 5K snippet payload (комфортно для агентского контекста). `tier="tier1"` — для запросов, где критична верифицированность источника.

### Truncation snippet

500 chars per result × 10 results = 5K — посильно. Brave `description` бывает до ~1500 chars; truncate в tool на `_WEB_MAX_SNIPPET_CHARS = 500` с маркером `…`.

### timeout_sec=20

Brave обычно отвечает за 1-3 сек. С учётом 1 retry на 429/5xx + sleep(1.5) + sleep(1.0) худший сценарий ~5 сек. 20 сек — щедрый запас на cold-start httpx или сетевые лаги.

## Что НЕ в этом PR

- **Anti-hallucination для web-цитат** (расширение `nefteboros/citations/` validator'а на формат `[Источник: <hostname>, web]` с проверкой по реальным `results`) — отдельный PR `feature/web-citations-validator`. Без него LLM теоретически может выдумать `[Источник: vedomosti.ru, web]` без реального hit'а; митигировано через `prompts/SYSTEM.md` (явное правило «только реальные hostname из results»).
- **LLM-translate узел** для перевода RU↔EN перед запросом — отвергнуто. Lang detection по кириллице направляет запрос в RU- или EN-источники без перевода. Если на golden-сценариях покажется регресс — вернёмся к идее.
- **E2E eval с golden-датасетом для web** — отвергнуто. Ground truth для свежих новостей нестабилен (через сутки результаты другие); количественная оценка web не входит в ТЗ §2.3. Ограничиваемся smoke-тестами + проверкой 5 golden-сценариев из ТЗ §4.6 руками.
- **Rate limiting на нашей стороне** — экспоненциальный backoff с 1 retry внутри `BraveClient`. Распределённый throttle — не нужен на free tier (1 RPS) и без multi-worker prod'а.
- **DuckDuckGo fallback** — отвергнуто. Если Brave упал / нет ключа — graceful error в JSON, как у `rag_search` без vectorstore. Пользователь видит понятный sigil, не «некорректный ответ из второго источника».
- **LangGraph узел `web_search`** — multi-tool архитектура (ADR-0018) не требует графа. Агент сам выбирает между `rag_search` / `analyst_query` / `web_search`.
- **UI-tab для web-результатов** — отдельный PR `feature/analyst-ui-widget`.

## Альтернативы рассмотренные

- **Сохранить upstream `web_search` как отдельный fallback при наличии OPENAI_API_KEY.** Отвергнуто: усложняет tool catalog (LLM видит два web-инструмента с разной семантикой), без `OPENAI_API_KEY` upstream бесполезен (а у нас его нет и не будет). Cleanup-стоимость низкая.
- **Реализовать через LangChain `BraveSearch`** community-обёртку. Отвергнуто: добавляет ~30 МБ langchain-community, не даёт нашей tier-фильтрации, не даёт lang-detection, не даёт нашего кэша. Сами пишем 6 файлов по ~50-150 строк — меньше чёрного ящика, легче тестировать.
- **Async `httpx.AsyncClient`** в `BraveClient`. Отвергнуто: Ouroboros tool-handler sync (`_tool_rag_search`/`_tool_analyst_query` тоже sync, последний оборачивает async через `asyncio.run`). Async без мотивации добавляет сложность.
- **Web как узел в `analyst_graph`** (паралельно forecast). Отвергнуто по тем же причинам, что и в ADR-0018: ТЗ §2.4 требует agent-level routing, не graph-level.
- **Per-host deduplication** (max 2 hits per host). Отвергнуто на v1 — Brave обычно даёт уникальные хосты, при шуме агент сам отбракует. Добавим, если на golden покажется проблемой.

## Последствия

**Плюсы:**
- ТЗ §2.3 / §2.4 закрыты — web-канал реален, цитирование структурировано.
- Lang detection делает RU-запрос полезным без отдельного перевода.
- Tier-фильтр + ENV-override даёт prod-гибкость без коммитов.
- Кэш на 1 час экономит free-tier лимит (1 RPS / 2000 mo) для повторных запросов в той же сессии.
- Multi-tool architecture (ADR-0018) расширилась естественно — без изменения existing tools.

**Минусы / риски:**
- **Brave free tier — 2000 запросов/месяц.** На демо/ревью хватит, но для прод-нагрузки нужен платный план или прокси-кэш.
- **Anti-hallucination для web-цитат не закрыт в этом PR.** Митигировано через `prompts/SYSTEM.md` (явное правило «только реальные hostname из results»); полноценный validator — отдельный PR.
- **Tool selection ambiguity** между `rag_search` и `web_search` для пограничных запросов (например «новости рынка нефти за месяц»). Решается:
  1. Чёткие `tool.description` с примерами «когда / когда НЕ» (написаны в plugin.py).
  2. `prompts/SYSTEM.md` — обновлён с явной таблицей tool selection и приоритизацией ТЗ §2.4.
  3. Combined ответы — агент может вызвать оба и синтезировать.
- **TIER lists могут устаревать.** Reuters купит кого-то, появится новый tier-1 — нужно править hostsets. ENV-override снимает остроту.

**Митигации:**
- В `prompts/SYSTEM.md` добавлена явная маркировка `[Источник: <hostname>, web]` и anti-hallucination правило.
- В `_tool_web_search` graceful degradation на любых ошибках (`BraveAuthError` / `BraveError` / `BraveRateLimitError` / общие исключения) → JSON `error`-поле, не raise.
- В changelog deployment notes — «после deploy: проверить `BRAVE_API_KEY` в `.env`, дёрнуть `/api/extensions/neftegaz_analyst/health`».

## Ссылки

- ТЗ: `docs/tz/original.md` §2.3 (web-поиск), §2.4 (приоритизация), §4.6 (демо-сценарии).
- ADR-0001 (fork-ouroboros): обоснование удаления upstream-функциональности.
- ADR-0007 (LLM-провайдеры): почему OPENAI_API_KEY не используется и upstream `web_search` нерелевантен.
- ADR-0018 (rag-search-tool): multi-tool architecture rationale.
- ADR-0019 (system-prompt-analyst): tool-selection промпт, расширенный в этом PR.
- Brave Search API: <https://api.search.brave.com/app/documentation/web-search/get-started>.
