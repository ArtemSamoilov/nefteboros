# ADR-0018 — RAG search как отдельный tool в neftegaz_analyst skill

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/rag-search-tool`
- **Связано:** ADR-0016 (forecast-skill, single-tool design); ADR-0014 (LangGraph subgraph); ADR-0009/0010/0011/0016-embed-retrieve (RAG pipeline)
- **Связанная серия экспериментов:** [docs/experiments/rag-full-eval-report.md](../experiments/rag-full-eval-report.md) — 8 итераций, chunk_hit@5 = 0.779

## Контекст

После merge ветки `feature/eval-rag-v2` (PR #16) у нас в `nefteboros/rag/retriever.py` есть production-ready Retriever (BGE-M3 + Chroma + опциональный topic-filter), измеренный на 95-Q датасете: `chunk_hit@5 = 0.779`, `source_hit@5 = 0.989`, `chunk_MRR = 0.527`. Vectorstore собран из 802 чанков 25 PDF-документов нефтегазового корпуса.

Однако **Ouroboros loop не видит RAG**. Существующий skill `neftegaz_analyst` (см. ADR-0016) экспортирует один tool `analyst_query` — обёртку над `analyst_graph` для forecast-pipeline'а. Граф знает только два пути: forecast_call и refusal. Documentary вопросы («Что говорит OPEC про квоты?», «Стратегия Новатэка по СПГ?») не имеют способа дотянуться до RAG.

## Проблема — почему «один tool» (ADR-0016) не масштабируется на RAG

ADR-0016 выбрал single-tool surface, аргументируя что «двойной surface = LLM выбирает между tool'ами по description'ам = best-effort, та же ловушка». Это валидно для forecast (где classifier даёт 0.98 type accuracy на 100-датасете).

Но для RAG этот аргумент **не работает**:

1. **Нет 5-правильной классификации.** Forecast classify_intent опирается на закрытое множество правил (#1-5) с предсказуемым покрытием. Documentary вопросы — **широкий открытый класс**: «факты из любого отчёта», «сравнения», «контекст», «стратегии». Rule-based classifier их не покроет.

2. **ТЗ §2.4 явно требует agent-level routing**:
   > «Логика приоритизации: 1) Сначала RAG; 2) Если данных достаточно — RAG как основной; 3) Web — как дополнение или для актуальности»
   
   Это **decision правил** для **агента**, не для kлассификатора в графе. Системный промпт агента — естественное место.

3. **ТЗ §2.5 явно делегирует выбор forecast агенту**:
   > «Агент должен уметь самостоятельно решать, когда вызывать [forecast] модуль»
   
   Аналогично должно быть для RAG и web — агент решает.

4. **ТЗ §4.6 требует комбинированный сценарий** (RAG + web). В графовой архитектуре это — параллельные ветки + merge — сильно усложняет дизайн. В agent-style — естественно: агент вызывает оба tool'а и синтезирует.

5. **Расширение на web-search (`feature/web-search-integration`)** в graph-only архитектуре потребовало бы ещё больше intent-types и узлов. К моменту web будет 3 ортогональных tool'а — classifier для них перестаёт работать.

## Решение

В `skills/neftegaz_analyst/plugin.py` зарегистрировать **второй tool** `rag_search` через `api.register_tool(...)`. Skill становится мульти-tool.

```python
def register(api):
    api.register_tool("analyst_query", _tool_analyst_query, ...)
    api.register_tool("rag_search",    _tool_rag_search,    ...)
    api.register_route("health", _route_health, methods=("GET",))
```

`_tool_rag_search` — тонкая обёртка над `nefteboros.rag.retriever.Retriever().retrieve()`:
- Lazy import (chromadb + sentence-transformers + torch не вытаскиваются на skill load)
- Defensive (любые ошибки → JSON `error`-поле, не raise — Ouroboros loop не падает)
- Возвращает JSON со списком top-k chunks: `text` (truncated до 4000 chars), `source_title`, `section_path`, `page_start`, `page_end`, `score`, `chunk_id`

**Что НЕ меняется:**
- `analyst_query` остаётся как был (форкаст и synthesis через граф)
- `analyst_graph` не получает узел `rag_retrieve` — RAG не fallback внутри графа
- Все 8 экспериментов RAG (см. `rag-full-eval-report.md`) — production default остаётся (heading prefix v2, без topic-filter)
- ADR-0016 пересмотрен **только** в части single-tool stance; lazy import / minimum-permissions / asyncio.run — остаются

**Кто будет решать когда вызвать какой tool:**
- Агент Ouroboros loop, видя `tool.description` обоих tools
- В `_RAG_TOOL_DESCRIPTION` явно прописано «ИСПОЛЬЗУЙ ПРИОРИТЕТНО на documentary вопросы», в `_TOOL_DESCRIPTION` analyst_query — «прогнозы цен и расчётный модуль»
- Окончательная приоритизация будет в системном промпте агента (PR `feature/system-prompt-analyst`, отдельно)

## Tool spec — критичные моменты

### `tool.description` для rag_search
~600 символов (в пределах OpenAI/Anthropic лимитов tool spec). Содержит:
- Описание корпуса (802 чанка из 25 документов с конкретным составом блоков)
- Когда вызывать (documentary факты, стратегии, санкции, фин. показатели)
- Когда НЕ вызывать (spot-цены, новости, прогнозы → analyst_query/web)
- Формат возврата (JSON со списком chunks)
- Подсказка по citation-стилю

Это **главное** место где агент-LLM получает инструкции — `SKILL.md` body не доходит до LLM (см. ADR-0014 §«Большая бомба I»).

### Schema
```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string", "description": "..."},
    "k": {"type": "integer", "minimum": 1, "maximum": 10}
  },
  "required": ["query"]
}
```

`k` — optional с default=5, max=10. Защита: больше 10 chunks × 4000 chars = 40K context — лишнее.

### Truncation chunk text
Чанки в нашем корпусе бывают до ~4000 токенов (особенно table_only из EI Stat Review). 5 chunks по 4000 = 20K токенов в tool response — много, но влезает в Kimi 2.6 (256K context). Truncate в tool на `_RAG_MAX_TEXT_CHARS = 4000` chars per chunk → защита от неуправляемой длины ответа.

### timeout_sec=30
RAG retrieval быстрый (BGE-M3 embed ~89ms на M-series CPU + Chroma search мгновенно). 30 секунд — щедрый запас на cold-start модели (~7 секунд при первом use).

## Что НЕ в этом PR

- **Системный промпт** для агента с приоритизацией (RAG → web → forecast) — отдельный PR `feature/system-prompt-analyst`. Без него агент в default режиме («I am self-modifying AI») не будет автоматически выбирать `rag_search` на documentary запросы.
- **`web_search` tool** — отдельный PR `feature/web-search-integration` после Brave/Tavily интеграции. Это будет третий tool в том же skill'е.
- **Combined synthesis** (RAG + web в одном ответе) — будет работать естественно когда агент видит несколько tools и может вызвать оба. Не требует отдельного кода в skill'е.
- **RAG-overlay в analyst_graph** для forecast-запросов с контекстом (например «Brent 3m с учётом OPEC+ решений в апреле») — отвергнуто как преждевременное усложнение; в backlog `feature/forecast-with-rag-context`.
- **Topic-filter режимы** в default — оставлены как опции через `Retriever.retrieve(topic_filter=...)`; на synthetic dataset дают регрессы, см. ADR-0016-embed-retrieve §«Calibration».
- **UI tab** для RAG-чата — отдельный PR `feature/analyst-ui-widget`.

## Альтернативы рассмотренные

- **`rag_search` как отдельный skill** (`skills/neftegaz_rag/`). Отвергнуто: PluginAPI v1 поддерживает несколько tools в одном skill'е через несколько `register_tool()` вызовов. Дополнительный skill = больше permissions / review pipeline / install flow без выгоды. Вся domain-logic нефтегаза остаётся в `neftegaz_analyst`.

- **RAG как узел в `analyst_graph`** (расширение существующего графа с новым `rag_retrieve` узлом). Отвергнуто: 
  1. Противоречит ТЗ §2.4 (RAG — **первичный** канал для documentary, не fallback после classifier'а);
  2. Усложняет classify_intent — нужно различать documentary vs forecast vs combined, а это не deterministic;
  3. Не масштабируется на web-search и combined ответы.

- **Один универсальный tool с роутингом внутри** (как сейчас `analyst_query`, но с RAG/web веткой). Отвергнуто по тем же причинам — single-tool design сохраняет ту же проблему «classifier должен решать всё».

- **Только `rag_search`, без `analyst_query`** (выкинуть граф). Отвергнуто: forecast часть работает (0.98 type accuracy), его удаление — destruction of working code без причины.

## Последствия

**Плюсы:**
- Documentary вопросы агенту наконец-то доступны через RAG (до этого PR — невозможно)
- Архитектура согласована с ТЗ §2.4 / §2.5 / §4.6
- Готова к расширению web-search'ем без переделки skill'а
- Минимальные изменения: добавили один handler + регистрацию, всё остальное работает как было

**Минусы / риски:**
- **Tool selection ambiguity** между `analyst_query` и `rag_search` для пограничных запросов (например «прогноз Brent с учётом санкций»). Решается:
  1. Чёткими `tool.description` (написаны в plugin.py с примерами «когда / когда НЕ»)
  2. Системным промптом (отдельный PR)
  3. Combined ответами — агент может вызвать оба
- **Vectorstore зависимость** — на сервере должен быть `data/vectorstore/` (~65 МБ, gitignored). Без него `_tool_rag_search` вернёт error. Deploy task — отдельная задача.
- **Cold-start** для rag_search — 7-10 секунд при первом вызове после Ouroboros restart (BGE-M3 model load). Последующие — <1 секунды.

**Митигации:**
- Documenting в SKILL.md «когда какой tool» с примерами
- В `_tool_rag_search` graceful degradation на любых ошибках — JSON `error`-поле, не raise
- В changelog deployment notes — «после deploy на сервер: scp vectorstore + проверить /api/extensions/neftegaz_analyst/health»

## Ссылки

- ТЗ: `docs/tz/original.md` §2.2 (RAG-модуль), §2.4 (приоритизация), §2.5 (forecast tool), §4.6 (демо-сценарии)
- ADR-0016 (forecast-skill): [docs/adr/0016-forecast-skill.md](0016-forecast-skill.md) — обоснование single-tool для forecast (по-прежнему валидно для analyst_query)
- ADR-0016-embed-retrieve: [docs/adr/0016-embed-retrieve.md](0016-embed-retrieve.md) — embedder/store/retriever архитектура
- Эксперимент: [docs/experiments/rag-full-eval-report.md](../experiments/rag-full-eval-report.md) — 8 итераций RAG, метрики, decision rationale
