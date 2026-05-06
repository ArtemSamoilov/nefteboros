# ADR-0011 — Чанкинг Markdown + tagging

- **Дата:** 2026-05-06
- **Статус:** Принято (после калибровки на 25 MD от Marker)
- **Контекст:** PR `feature/rag-chunk` (этап 2 из 3 в RAG-pipeline)
- **Связано:** ADR-0009 (corpus), ADR-0010 (Marker), будущий ADR-0012 (embed + retrieve)

## Контекст

После PR A (`feature/rag-extract`) у нас на диске лежат 25 Markdown-файлов в `data/markdown/<source_id>.md`, сгенерированных Marker'ом из PDF. Каждый MD — структурированный текст с заголовками, таблицами и страничными маркерами `{N}`.

Задача PR B: **разбить эти MD на чанки и навесить метаданные** так, чтобы:
1. Retrieval (PR C) находил релевантный кусок для конкретного вопроса
2. Каждый чанк нёс достаточный контекст для LLM-синтеза (Kimi 2.6 — 256k window, переоценивать дробление не надо)
3. Citations работали — agent знает страницу и раздел источника
4. Можно было фильтровать по metadata (например, `region=russia AND geopolitics=sanctions`)

## Решение

### 1. Размер чанков — крупные

**Target 3000 токенов, max 4000, overlap 200** (только если режем по причине превышения max).

Обоснование:
- Kimi 2.6 — 256k context, мощная модель. Мелкие чанки (200-500 токенов) дают шум: ретривер тащит 5-10 кусков, LLM «запутывается» в фрагментации.
- Большие чанки сохраняют локальный контекст: «весь раздел про OPEC+ Compliance Q1 2026» как единый смысловой блок.
- Overhead на embedding meньше: меньше чанков = меньше векторов в Chroma = быстрее retrieval.

Альтернатива «1000 токенов / 200 overlap» (стандарт) — отвергнута: Артём прямо обозначил «не мельчить под Kimi 2.6».

### 2. Heading-aware splitting

Режем **только по `## Heading 2`** (или `### Heading 3` если H2-блок > max). Не режем по фиксированному окну токенов внутри связного раздела — это всегда хуже семантического разреза.

Алгоритм:
```
parse MD → AST из (Heading, Paragraph, Table, CodeBlock, ...) элементов с page-маркерами
group AST в блоки по H2-границам
для каждого H2-блока:
    если token_count <= max → один chunk
    иначе → подрезать по H3-границам
        если H3-блок всё ещё > max → подрезать по параграфам (с overlap=200)
```

Никогда не режем **внутри одного параграфа или таблицы**.

### 3. Спецлогика для таблиц

Таблицы — критичный кейс нашего корпуса (OPEC ASB, EI Stat Review, отчёты компаний). Правила:

| Размер таблицы | Стратегия |
|---|---|
| ≤ max (4000 токенов) | оставить как часть chunk'а, не резать |
| > max | вынести таблицу в **отдельный chunk** с типом `table-only`. **Header строки дублируем** в каждый фрагмент если приходится резать дальше |
| header-row + 1+ data-rows должны жить вместе | даже если выходит за overlap |

В schema chunk'а — флаг `has_table: bool` и `is_table_only: bool` для будущего query-time routing'а.

### 4. Page tracking

Marker оставляет page-маркеры `{N}` в MD (см. ADR-0010, `paginate_output=True`). При chunking:
- Парсим маркеры и сохраняем `page_start` / `page_end` в metadata чанка
- Стриппим маркеры из `text` (агенту не нужны в LLM-контексте)

Это даёт citations вида: «по данным OPEC MOMR Apr 2026, стр. 47-49».

### 5. Tagging — три уровня

**Source-tags** (детерминированно из manifest.yml):
```python
{
  "source_id": "gov_rf_energostrategy_2050",
  "source_title": "Энергетическая стратегия РФ до 2050 года",
  "publisher": "Правительство РФ",
  "block": "1_strategy",
  "type": "government_strategy",
  "language": "ru",
  "date": "2025-04-12",
}
```

**Section-tags** (из MD-структуры heading hierarchy):
```python
{
  "headings": ["3. Production Outlook", "3.2 OPEC Production"],
  "section_path": "3. Production Outlook > 3.2 OPEC Production",
  "page_start": 47,
  "page_end": 49,
  "has_table": true,
  "is_table_only": false,
}
```

**Topic-tags** (закрытый словарь, LLM-классификация):

Cловарь — 5 осей × 22 значения, см. `nefteboros/rag/topic_vocabulary.py`:
```yaml
energy:        [oil, gas, lng, oil_products]              # 4
market_aspect: [supply, demand, prices, inventories,      # 6
                trade, infrastructure]
geopolitics:   [sanctions, conflicts, energy_security]    # 3
finance:       [corporate_finance, government_finance,    # 4
                strategy, forecast]
region:        [russia, europe, us, middle_east, asia]    # 5
```

Каждый чанк имеет 0-3 значения **на каждой оси**. Хранится как `topic_<axis>: list[str]`.

Tagging выполняется через **kimi-k2p6** (через HydraGPT, см. ADR-0007) с structured-JSON промптом и валидацией против словаря. Артём явно выбрал kimi вместо glm-5 — токены через HydraGPT неограничены, выигрыш по точности классификации важнее скорости/стоимости. Concurrency=20 (ограничено semaphore'ом в `tagger.py`).

Robustness:
- При невалидном JSON-ответе — 3 retry с экспоненциальной задержкой
- При окончательном провале — пустые tags + log warning (теги best-effort, не критичны для bi-encoder retrieval, который работает и по семантике текста)
- Парсер толерантен к code-block обёртке (\`\`\`json …\`\`\`)
- Все значения проходят `filter_valid()` против словаря — невалидные отбрасываются молча (защита от LLM-галлюцинаций тегов)

## Architecture

```
nefteboros/rag/
  schema.py             — Pydantic: Chunk, ChunkMetadata
  topic_vocabulary.py   — закрытый словарь topic-tags
  chunker.py            — MD → list[Chunk]:
                            - parse_markdown(text) -> AST
                            - split_by_headings(ast, max_tokens) -> blocks
                            - extract_tables(blocks) -> spec rules
                            - assemble_chunks(blocks, source_meta) -> list[Chunk]
  tagger.py             — assign topic-tags via LLM
  pipeline.py           — оркестратор: read MD → chunk → tag → save (для PR C)
scripts/
  chunk_corpus.py       — CLI: data/markdown/*.md → data/chunks/<source_id>.jsonl
```

## Где именно мы НЕ режем (важно)

- **Внутри параграфа** — никогда
- **Внутри таблицы** — только если она сама >max, и тогда дублируем header
- **Внутри code block** (`<code>...`) — никогда (но в нашем корпусе их почти нет)
- **Внутри одного MD-blockquote** — никогда (комментарии регулирующих органов часто оформлены так)

## Что НЕ в этом PR

- Эмбеддинги (BGE-M3) → PR C `feature/rag-embed-retrieve` + ADR-0012
- Chroma persist → PR C
- Retrieval + bge-reranker → PR C
- Tool wrapper для агента → PR C
- Eval-метрики chunking (avg chunk size, % таблиц, distribution) → отдельный PR `feature/eval-rag` + dataset

## Альтернативы рассмотренные

- **RecursiveCharacterTextSplitter (LangChain)** — фиксированное окно токенов, плохо для нашего корпуса с heading-структурой и таблицами.
- **Sentence-aware splitter** — даёт мелкие чанки, противоречит «не мельчить под Kimi 2.6».
- **Семантическое чанкование через embeddings** (semantic chunker) — дорого ($), сложно отлаживать, не сильно выигрывает над heading-aware на нашем структурированном корпусе.
- **Free-form topic tags вместо закрытого словаря** — отвергнуто: словарь даёт filterable retrieval и evals.
- **NER-extraction для entity-tags (компании/страны/проекты)** — отложено в backlog v1.x; topic-tags + section-headings уже дают сильный контекст.

## Калибровка — фактические находки на 25 MD

**Page-маркеры:** Marker выдаёт их как `{N}` (одна цифра в фигурных скобках), парсер `_split_into_lines_with_pages` корректно подхватывает. Покрытие — 100% чанков получили `page_start`.

**Таблицы — обнаружен Marker artifact для широких таблиц.** EI Statistical Review (74-е издание) содержит исторические серии в таблицах с десятками колонок (страны × годы). Marker сворачивает их в **одну MD-row с многострочными ячейками через `<br>`** — например:

```
| Mexico<br>US | 7.92<br>92.43 | 7.88<br>91.56 | ... |
```

Прямой раздел по `\n` даёт огромные «строки» по 10K токенов каждая (>BGE-M3 limit 8192). Решение в `_split_table_block`:
- Если таблица имеет ≤2 строк (giant single-row): разворачиваем `<br>` в `\n`, режем как плоский текст
- Если есть row с самостоятельным token-count > max_tokens: то же самое
- Если header (первые 2 строки) занимает >40% max_tokens: тот же путь
- Если общий объём >4× max_tokens с >50 `<br>`: тот же путь

В этом режиме теряем визуальную структуру таблицы (`|...|`), но сохраняем семантический контент — для retrieval/embedding достаточно.

**Финальная статистика на 25 MD:**

| Метрика | Значение |
|---|---|
| Total chunks | 802 |
| Total tokens | 2 433 183 |
| min / median / p95 / max | 4 / 3287 / 3995 / 4031 |
| Mean ± stdev | 3034 ± 1027 |
| Chunks > 8192 (BGE-M3 limit) | 0 (0%) ✓ |
| Chunks < 200 (short) | 24 (3%) — в основном footnotes таблиц («**Source:** Includes data from FGE Iran Service»), оставлены — релевантны для retrieval |
| Chunks с has_table | ~78% |
| Chunks с is_table_only | ~50% |
| Покрытие page_start | 100% |

**Размеры таблиц на источник** распределились в пределах [≤4031], кроме ситуаций когда плоский режим резал длинную таблицу — тогда несколько чанков подряд по 3000-4000 токенов с одной section_path.

## Ссылки

- ADR-0010: [docs/adr/0010-pdf-to-markdown-marker.md](0010-pdf-to-markdown-marker.md)
- ADR-0009: [docs/adr/0009-corpus-strategy.md](0009-corpus-strategy.md)
- ADR-0007: [docs/adr/0007-llm-providers.md](0007-llm-providers.md) — GigaChat + HydraGPT
