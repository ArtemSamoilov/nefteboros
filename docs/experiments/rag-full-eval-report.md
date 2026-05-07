# RAG retriever — полный отчёт по экспериментам

> Прозрачная документация всех экспериментов оценки и оптимизации RAG retriever'а проекта nefteboros. Если хочешь короче — см. `rag-baseline.md` (только baseline) и `rag-prefix-experiments.md` (только улучшения). Этот документ объединяет всё в одну хронологическую историю.

- **Период:** 2026-05-06 — 2026-05-07
- **Ветки/PR:** `feature/rag-embed-retrieve` (#15) → `feature/eval-rag-v2` (#16)
- **Связанные ADR:** ADR-0009 (corpus), ADR-0010 (Marker), ADR-0011 (chunking), ADR-0016 (embed + retrieve)

---

## 1. Цели

После сборки корпуса (25 PDF → 802 tagged chunks → BGE-M3 эмбеддинги в ChromaDB) нужно было:
1. Измерить **базовое качество retrieval'а**
2. Найти **узкие места** через failure analysis
3. **Итеративно улучшать** retriever, измеряя каждое изменение
4. Зафиксировать **production-конфигурацию** с известными ограничениями

Главный нерешённый вопрос: на сколько мы можем поднять `chunk_hit@5` без принципиально нового подхода (другая embedding модель / hybrid BM25 / HyDE)?

---

## 2. Setup

### 2.1 Корпус и vectorstore

- **25 PDF**, ~135 МБ — стратегические документы РФ + операционка глобальных рынков + корпоративные отчёты + геополитика
- **802 чанка** после chunker'а с 4-уровневой классификацией: source / section (heading-path + page) / topic-tags (5 осей × 22 значения) / `is_table_only`/`has_table`
- **BGE-M3** (1024-dim, multilingual), **ChromaDB persistent** (cosine), max_seq_length=4096

### 2.2 Eval датасет

- **Файл:** `datasets/rag_eval/v1.jsonl`, **95 вопросов**
- **Метод:** semi-synthetic. Стратифицированно сэмплировали 4 чанка ≥500 токенов из каждого из 25 source. Для каждого через kimi-k2p6 сгенерировали один реалистичный вопрос (от лица топ-менеджера банка) + краткий ground truth answer
- **Распределение:** EN 55 / RU 40, по блокам: strategy 39, corporate 28, operational 17, geopolitics 11
- **Известные ограничения** (см. секцию 9):
  - Synthetic bias — формулировки лексически близки к chunk text
  - Source-leak — часть Q упоминают источник (`Bruegel's recent paper`)
  - Один правильный chunk на вопрос (не учитываем multi-relevant)

### 2.3 Метрики

| Метрика | Что значит |
|---|---|
| **chunk_hit@k** | Доля Q где правильный chunk_id попал в top-k |
| **source_hit@k** | Доля Q где правильный source_id (документ) попал в top-k (loose match — много chunks одного source) |
| **chunk_MRR** | Mean Reciprocal Rank по chunk_id |
| **source_MRR** | MRR по source_id |

Слайсы: по `language` (ru/en), по `block` (1_strategy / 2_corporate / 3_operational / 4_geopolitics), по `content_type` (text_only / with_table / table_only).

### 2.4 Конфигурация инфраструктуры

| Этап | Где запускается | Замер |
|---|---|---|
| Build_index (embedding 802 чанков) | NVIDIA GPU 8 ГБ off-server | ~12 мин |
| Query embedding (на 4 ГБ сервере 2 vCPU) | CPU | прогноз median 300-500 мс (по бенчу Mac M-series → 89 мс) |
| Reranker bge-reranker-v2-m3 | **не запускается** ни на NVIDIA 8 ГБ (VRAM OOM при 2 моделях), ни на Mac (32 GB attention OOM на subset) | отключён в default |
| LLM call (kimi через HydraGPT) | сервер | ~5 сек на запрос (для query classifier) |

---

## 3. Хронология экспериментов

### 3.1 v1 baseline — embedding only

**Конфиг:** в Chroma идёт сырой `chunk.text` без префикса. Retrieval = top-5 cosine.

**Реализация:**
```python
def text_for_embedding(self):
    return self.text  # raw content
```

**Результат:**

| Метрика | Значение |
|---|---:|
| chunk_hit@1 | 0.326 |
| chunk_hit@3 | 0.526 |
| chunk_hit@5 | **0.653** |
| chunk_hit@10 | 0.737 |
| chunk_MRR | 0.458 |
| source_hit@5 | **0.979** |
| source_MRR | 0.883 |

**По блокам:**

| Блок | chunk_hit@5 |
|---|---:|
| 1_strategy | 0.769 |
| 2_corporate | 0.429 ⚠ |
| 3_operational | 0.588 |
| 4_geopolitics | 0.909 |

**По типу контента:**

| Тип | chunk_hit@5 |
|---|---:|
| text_only | 0.649 |
| with_table | 0.814 |
| **table_only** | **0.200** ⚠⚠ |

### 3.2 Failure analysis — что именно не так

| Категория промаха | Кол-во | % |
|---|---:|---:|
| CHUNK_HIT (попал в top-5) | 62 | 65.3% |
| SAME_DOC_MISS (правильный source в top-5, но не chunk) | 31 | **32.6%** |
| CROSS_DOC_MISS (даже source не нашёлся) | 2 | 2.1% |

**Главные выводы:**
1. **Embedding почти всегда находит правильный документ** (source_hit@5 = 98%). Cross-doc miss всего 2%.
2. Проблема — **гранулярность** retrieval'а **внутри** документа. AR компаний (Газпром, Роснефть, Лукойл, Новатэк, Татнефть) имеют много semantically близких chunks → правильный путается с соседями.
3. **Table_only — катастрофа** (20% chunk_hit@5). После моего fallback-режима (`<br>` → `\n` для широких таблиц) контент превращается в `Mexico US 7.92 92.43 ...` — для BGE-M3 это семантический мусор.

**Худшие источники (chunk_miss rate):**
- ei_statistical_review_2025 — 100%
- opec_asb_2024 — 100%
- rosneft_ar_2024 — 100%
- gazprom_accounting_2024 — 75%
- novatek_ar_2024 — 75%

### 3.3 Гипотеза 1 — heading prefix даст semantic context

**Идея:** добавить `[source_title]` + `section_path` перед chunk text. BGE-M3 получит контекст «откуда», особенно полезный для table_only chunks где content = поток цифр.

### 3.4 v2 — simple heading prefix

**Реализация:**
```python
def text_for_embedding(self, with_heading_prefix=True):
    sp = self.section_path or "(no section)"
    return f"[{self.source_title}]\n{sp}\n\n{self.text}"
```

**Результат:**

| Метрика | v1 | **v2** | Δ |
|---|---:|---:|---:|
| chunk_hit@1 | 0.326 | 0.347 | +2.1 |
| chunk_hit@3 | 0.526 | **0.674** | **+14.8** |
| chunk_hit@5 | 0.653 | **0.779** | **+12.6** |
| chunk_hit@10 | 0.737 | 0.874 | +13.7 |
| chunk_MRR | 0.458 | 0.527 | +6.9 |
| source_hit@5 | 0.979 | 0.989 | +1.0 |

**По типу контента:**

| Тип | v1 | **v2** | Δ |
|---|---:|---:|---:|
| text_only | 0.649 | **0.757** | +10.8 |
| with_table | 0.814 | 0.837 | +2.3 |
| **table_only** | **0.200** | **0.667** | **+46.7** ⭐ |

**По блокам:**

| Блок | v1 | v2 | Δ |
|---|---:|---:|---:|
| 1_strategy | 0.769 | **0.897** | +12.8 |
| **2_corporate** | **0.429** | **0.607** | **+17.8** |
| 3_operational | 0.588 | 0.706 | +11.8 |
| 4_geopolitics | 0.909 | 0.909 | 0 |

**Гипотеза подтверждена.** Главный win — table_only +46.7 п.п. Корпоративка тоже значимо лучше.

### 3.5 Failure analysis v2 — что осталось

| Категория | v1 | v2 |
|---|---:|---:|
| CHUNK_HIT | 65.3% | **77.9%** |
| SAME_DOC_MISS | 32.6% | 21.1% |
| CROSS_DOC_MISS | 2.1% | 1.1% |

**Найдены 2 root cause'а:**

**(a) Chunker bug — section_path не соответствует content.** Пример Роснефть AR:
```
section_path: "ЛИДЕР РОССИЙСКОЙ НЕФТЯНОЙ ОТРАСЛИ > ПРОМЫШЛЕННАЯ БЕЗОПАСНОСТЬ"
text:         "Переработка и коммерция. Объем переработки нефти..."
```

Headings tracking в chunker берёт стек только из начала chunk'а. Если внутри chunk'а есть bold/CAPS sub-headings (не parsed как H2) — они теряются.

**(b) HTML-мусор в section_path.** Marker оставляет `<span id="page-71-0"></span>3 > Догазификация`. Эти теги попадают в prefix.

**(c) Дубликат source_title.** Headings часто начинаются с названия документа: `Годовой отчет ПАО «Газпром» за 2024 год > Стратегия`. В prefix `[Газпром AR 2024]` + section_path — дубль.

**(d) Cross-doc miss Роснефть IFRS vs AR (cash flow query).** В AR Роснефти есть выжимка финансов с тем же лексикомом — embedding не отличает от полной МСФО.

### 3.6 v3 — enriched prefix (HTML clean + dedup + first_meaningful_line)

**Идея:** убрать мусор из section_path и добавить `>>> {first_meaningful_line}` как реальный sub-section content.

**Реализация:**
```python
def _clean_heading(text):
    return re.sub(r"<[^>]+>", "", text).strip()

def _first_meaningful_line(text):
    # первая non-table, non-page-marker, ≥10 chars строка
    ...

def text_for_embedding(self, with_heading_prefix=True):
    sp = _clean_heading(self.section_path)
    # dedup source_title из section_path
    ...
    first_line = _first_meaningful_line(self.text)
    return f"[{self.source_title}]\n{sp}\n>>> {first_line}\n\n{self.text}"
```

**Результат:**

| Метрика | v2 | **v3** | Δ |
|---|---:|---:|---:|
| chunk_hit@1 | 0.347 | **0.421** | +7.4 |
| chunk_hit@3 | 0.674 | 0.611 | -6.3 |
| chunk_hit@5 | **0.779** | 0.768 | -1.1 |
| chunk_hit@10 | 0.874 | **0.905** | +3.1 |
| chunk_MRR | 0.527 | **0.553** | +2.6 |

**По типу контента:**

| Тип | v2 | v3 | Δ |
|---|---:|---:|---:|
| text_only | 0.757 | 0.703 | **-5.4** ⚠ |
| with_table | 0.837 | **0.884** | +4.7 |
| **table_only** | **0.667** | 0.600 | -6.7 ⚠ |

**Смешанный результат.** v3 «острее»: лучше top-1 и top-10, хуже середина. `>>> first_meaningful_line` помогает на with_table, но **вредит на text_only** (общие первые фразы отвлекают embedder) и **table_only** (caption уже в section_path → дублирование).

### 3.7 v4 — clean без first_line

**Идея:** оставить HTML-cleanup + dedup, убрать first_meaningful_line.

**Результат:**

| Метрика | v2 | v3 | **v4** |
|---|---:|---:|---:|
| chunk_hit@5 | **0.779** | 0.768 | 0.758 |
| chunk_hit@10 | 0.874 | **0.905** | 0.895 |

**v4 хуже v2 на 2.1 п.п.** Контр-интуитивно — чистка ухудшила результат. Гипотеза:
- HTML-теги `<span id="page-N">` несли **page-сигнал** для embedding'а
- Двойное упоминание source_title (в `[...]` + в section_path) **усиливало identity** документа

**Решение по prefix:** v2 (simple) — production default. Откатываем v3/v4 logic в schema.py.

### 3.8 Гипотеза 2 — query classifier для cross-doc решения

**Идея:** classify query → topic-tags / document-type → filter или boost retrieval results. У нас уже есть теги в metadata chunks (5 осей × 22 значения), но в retrieval не используются.

Для cross-doc Роснефть IFRS — ожидание: query classify → `type: financial_report` → filter отрежет AR chunks → IFRS поднимется в top-5.

### 3.9 v2 + topic-boost (boost по topic-tags)

**Реализация:**
- Classify query через kimi → `TopicTags`
- Retrieve top-30 (без filter)
- For each: `new_score = bi_score + 0.05 * topic_overlap_count`
- Sort, top-5

**Результат:**

| Метрика | v2 | **v2+topic-boost** | Δ |
|---|---:|---:|---:|
| chunk_hit@1 | 0.347 | **0.400** | +5.3 |
| chunk_hit@3 | 0.674 | 0.684 | +1.0 |
| chunk_hit@5 | **0.779** | 0.768 | -1.1 |
| chunk_hit@10 | 0.874 | 0.884 | +1.0 |
| chunk_MRR | 0.527 | **0.563** | +3.6 |
| source_hit@10 | 0.989 | **1.000** | +1.1 |

Trade-off: **chunk_hit@1 +5.3 п.п.**, source_hit@10 = 100%, но **chunk_hit@5 -1.1**.

### 3.10 v2 + topic-filter (strict с fallback)

**Реализация:**
- Retrieve top-30
- Filter: оставить только chunks с ≥1 matching topic-tag
- Fallback: если получилось < k_final — добавить unfiltered

**Результат:** идентичен topic-boost (-1.1 на chunk_hit@5). Strict отбрасывает релевантные chunks с неполными тегами от tagger'а.

### 3.11 v2 + doc-type strict (Chroma server-side filter)

**Идея:** classifier → `list[str]` of doc_types → Chroma `where={"type": {"$in": [...]}}` — фильтр на стороне базы до retrieval.

**Реализация (`query_classifier.classify_doc_types_async`)**: prompt со списком 12 типов из manifest (`annual_report`, `financial_report`, `government_strategy`, `market_report` etc.) + описаниями. **Strict prompt** — «выбирай 1-2 точных типа, никаких "на всякий случай"».

**Результат на критическом case:**

```
Q: «Каков чистый денежный поток Роснефти за 2024?»
classifier: ['financial_report']  ✓
target rosneft_ifrs at position: 3 ✓ (раньше не находился)
top-5: [gazprom_accounting × 2, rosneft_ifrs × 3]
```

**Точечная победа.** Но overall:

| Метрика | v2 | **v2 + doc-type strict** | Δ |
|---|---:|---:|---:|
| chunk_hit@5 | 0.779 | 0.663 | **-11.6** ⚠ |
| chunk_hit@10 | 0.874 | 0.758 | -11.6 |
| **source_hit@5** | **0.989** | **0.842** | **-14.7** ⚠⚠ |
| chunk_MRR | 0.527 | 0.477 | -5.0 |

**Серьёзный регресс.** source_hit@5 упал с 99% до 84% — classifier ошибается с типом для ~15% запросов, и Chroma `where` полностью отрезает правильный source.

### 3.12 v2 + doc-type boost (soft)

**Идея:** заменить strict filter на soft boost — bonus +0.10 за совпадение `chunk.type ∈ predicted_types` без отрезания.

**Результат:**

| Метрика | v2 | doc-type strict | **doc-type boost** |
|---|---:|---:|---:|
| chunk_hit@5 | **0.779** | 0.663 | 0.705 |
| source_hit@5 | **0.989** | 0.842 | 0.905 |
| chunk_MRR | **0.527** | 0.477 | 0.500 |

**Smoother чем strict, но всё равно регресс.** Bonus +0.10 для chunks правильного type перевешивает базовое embedding-преимущество правильного chunk'а в неправильном type.

---

## 4. Сводная таблица всех 8 экспериментов

| # | Конфиг | chunk_hit@5 | chunk_MRR | source_hit@5 | Latency | Прим. |
|---|---|---:|---:|---:|---|---|
| 1 | v1 baseline | 0.653 | 0.458 | 0.979 | fast | embedding only |
| 2 | **v2 simple prefix** | **0.779** | 0.527 | **0.989** | fast | ✅ **production** |
| 3 | v3 enriched prefix | 0.768 | 0.553 | 0.989 | fast | регресс text_only/table_only |
| 4 | v4 clean prefix | 0.758 | 0.528 | 0.989 | fast | чистка ослабила сигнал |
| 5 | v2 + topic-boost | 0.768 | 0.563 | 0.989 | +5 сек | trade-off (+hit@1, -hit@5) |
| 6 | v2 + topic-filter | 0.768 | 0.526 | 0.989 | +5 сек | regression |
| 7 | v2 + doc-type strict | 0.663 | 0.477 | 0.842 | +5 сек | strong regression |
| 8 | v2 + doc-type boost | 0.705 | 0.500 | 0.905 | +5 сек | softer regression |

**Победитель по chunk_hit@5: v2 simple prefix (0.779).**

### 4.1 Slice-level top results

| Slice | Best config | Best chunk_hit@5 |
|---|---|---:|
| Overall | v2 | 0.779 |
| EN | v2 | 0.855 |
| RU | v2 | 0.675 |
| 1_strategy | v2 | 0.897 |
| 2_corporate | v2 | 0.607 |
| 3_operational | v2 | 0.706 |
| 4_geopolitics | v2 / v3 / v4 | 0.909 |
| text_only | v2 | 0.757 |
| with_table | v3 (+first_line) | 0.884 |
| table_only | v2 | 0.667 |

---

## 5. Ключевые выводы

### 5.1 Что сработало
1. **Heading prefix `[source_title]` + `section_path`** — главный win (+12.6 п.п. на chunk_hit@5, +47 п.п. на table_only)
2. Embedding **почти не теряет правильный источник** (source_hit@5 = 99%) — это позволяет агенту работать с расширенным контекстом из top-5
3. **BGE-M3 на CPU укладывается в сервер 4 ГБ** (RSS ~2 ГБ, latency 89 мс per query на M-series)

### 5.2 Что не сработало (и почему)
1. **first_meaningful_line (v3)** — помог with_table, но навредил text_only (общие фразы отвлекают) и table_only (дублирование с section_path)
2. **HTML cleanup и dedup source_title (v4)** — убрали полезный сигнал. HTML-теги несли page-context, двойной title усиливал identity
3. **Topic-tags filter/boost** — marginal trade-off (+hit@1 за счёт hit@5)
4. **Doc-type filter** — на synthetic dataset даёт серьёзный регресс (-11 п.п. chunk_hit@5, -15 п.п. source_hit@5). Точечно решает Роснефть IFRS, но classifier ошибается на 15% и filter отрезает правильный source

### 5.3 Главный мета-вывод
**«Меньше manipulations — лучше для BGE-M3».** Простой prefix оказался устойчивее всех «умных» enhancements. Эвристики (cleanup, first_line) и filters (topic, doc-type) рискуют убрать сигнал, который embedding model использует.

### 5.4 Известные ограничения
1. **Synthetic dataset bias** — Q сгенерены kimi по chunk text, лексика близка → завышает метрики
2. **Source-leak** в формулировках (`Bruegel's recent paper`) → завышает source_hit
3. **Один правильный chunk per Q** — не measurable multi-relevant retrieval (recall@k неинформативен)
4. **Reranker bge-reranker-v2-m3 нельзя замерить** — не помещается ни на NVIDIA 8 ГБ, ни на Mac MPS (OOM на attention buffer для длинных pairs)
5. **Chunker bug** — section_path иногда не соответствует content (bold/CAPS sub-headings не parsed как H2). Это backlog `feature/chunker-fix`.

---

## 6. Production decision

**Default config:**
```python
# nefteboros/rag/schema.py
def text_for_embedding(self, with_heading_prefix=True):
    sp = self.section_path or "(no section)"
    return f"[{self.source_title}]\n{sp}\n\n{self.text}"

# nefteboros/rag/retriever.py
def retrieve(query, k_dense=30, k_final=5, rerank=False, topic_filter='off'):
    ...
```

**Почему именно так:**
- **`with_heading_prefix=True`** — даёт +12.6 п.п. chunk_hit@5 без latency cost
- **`rerank=False`** — bge-reranker-v2-m3 не помещается на сервер 4 ГБ
- **`topic_filter='off'`** — на synthetic dataset даёт регресс; могут вернуть на manual eval

**Production-конфигурация подтверждена** на bench:
- Build_index: ~12 мин (off-server NVIDIA 8 ГБ, batch=8)
- Server query latency: ~300-500 мс (прогноз для 2 vCPU 4 ГБ из M-series CPU bench)
- RSS на сервере: ~2 ГБ под BGE-M3 + ~500 МБ Ouroboros core ≈ 2.5 ГБ из 4

---

## 7. Что в production (артефакты)

### Код
- `nefteboros/rag/schema.py` — `Chunk.text_for_embedding(with_heading_prefix=True)` default
- `nefteboros/rag/embedder.py` — singleton BGE-M3 (max_seq=4096, batch=1 default; на CUDA можно поднимать через env)
- `nefteboros/rag/store.py` — ChromaDB persistent с metadata-фильтрами
- `nefteboros/rag/retriever.py` — bi-encoder + опциональные `topic_filter` режимы (off/boost/filter/doc-type/doc-type-boost)
- `nefteboros/rag/query_classifier.py` — async kimi-k2p6 classifier (TopicTags + doc_types)
- `scripts/build_index.py` — идемпотентный CLI (`--with-heading-prefix` default True)
- `scripts/eval/build_rag_eval_dataset.py` — генератор semi-synthetic Q
- `scripts/eval/eval_rag.py` — метрики hit@k(1,3,5,10) + MRR + слайсы
- `scripts/eval/analyze_rag_failures.py` — failure breakdown (CHUNK_HIT/SAME_DOC_MISS/CROSS_DOC_MISS, by content type / size / source)

### Данные
- `data/chunks/*.jsonl` — 802 tagged chunks (gitignored, 11 МБ)
- `data/vectorstore/` — ChromaDB persistent (gitignored, ~65 МБ)
- `datasets/rag_eval/v1.jsonl` — 95 semi-synthetic Q (в репо)

### Метрики (`metrics/runs/`)
- `2026-05-07_rag_baseline_bi_8a3cf49.json` — v1 baseline
- `2026-05-07_rag_baseline_bi_e3c3c0c.json` — v2 simple prefix
- `2026-05-07_rag_baseline_bi_ca64918.json` — v3 enriched
- `2026-05-07_rag_baseline_bi_5f0b45d.json` — v4 clean
- `2026-05-07_rag_baseline_bi_topic_boost_*.json` — topic boost
- `2026-05-07_rag_baseline_bi_topic_filter_*.json` — topic filter
- `2026-05-07_rag_baseline_bi_doc_type_*.json` — doc-type strict
- `2026-05-07_rag_baseline_bi_doc_type_boost_*.json` — doc-type boost

### Документация
- `docs/adr/0009-corpus-strategy.md`
- `docs/adr/0010-pdf-to-markdown-marker.md`
- `docs/adr/0011-chunking-and-tagging.md`
- `docs/adr/0016-embed-retrieve.md`
- `docs/experiments/rag-baseline.md` — детальный отчёт по v1
- `docs/experiments/rag-prefix-experiments.md` — детальный отчёт по v2-v4 + filter experiments
- `docs/experiments/rag-full-eval-report.md` — **этот документ** (объединяющий)

---

## 8. Backlog для дальнейшего улучшения

По убыванию ожидаемого прироста:

| # | Эксперимент | Ожидаемый эффект | Стоимость |
|---|---|---|---|
| 1 | **Manual eval dataset** (30-50 Q от человека, не знающего корпуса) | Корректная оценка doc-type filter, реальная prod-метрика. Может разблокировать включение doc-type в default | 2-3 часа Артёма |
| 2 | **HyDE для RU** — kimi генерирует «гипотетический ответ» → эмбеддим его | +5-15 п.п. на RU chunk_hit@5 (сейчас 67% vs EN 85%) | 4-6 часов кода + eval |
| 3 | **Hybrid BM25 + dense retrieval** — для точных терминов (тикеры, имена компаний, цифровые показатели) | +3-7 п.п. на финансовых/корпоративных запросах | 1 день |
| 4 | **Fix chunker bug** — отслеживать bold/CAPS sub-headings внутри chunk'а, обновлять section_path | +5-10 п.п. (после ребилда chunks) | 4-6 часов + регенерация tagger |
| 5 | **LLM-rerank через kimi** — замена bge-reranker-v2-m3 на сервере (не помещается в RAM) | precision boost для top-5, особенно на cross-doc | 3-4 часа |
| 6 | **Polychunk ground truth** + recall@k метрика | Точнее метрика для AR с similar chunks | 1 день (требует разметки) |

---

## 9. Воспроизводимость

```bash
# 1. Чанки и vectorstore (предполагается данные на месте)
ls data/chunks/*.jsonl | wc -l   # 25
python3 scripts/build_index.py   # default = with_heading_prefix=True
                                  # ~12 мин на NVIDIA 8 ГБ
                                  # ~1.5-2 часа на CPU/MPS

# 2. Eval по конкретной конфигурации
python3 scripts/eval/eval_rag.py --version v1                                # baseline (v2 prefix default)
python3 scripts/eval/eval_rag.py --version v1 --config bi+topic-boost        # +topic boost
python3 scripts/eval/eval_rag.py --version v1 --config bi+doc-type-boost     # +doc-type boost
python3 scripts/eval/eval_rag.py --version v1 --config bi+rerank             # +cross-encoder rerank (если влезает)

# 3. Failure analysis
python3 scripts/eval/analyze_rag_failures.py --version v1 --dump-misses /tmp/misses.tsv

# 4. Сгенерировать новый датасет (semi-synthetic, requires HYDRA_API_KEY)
python3 scripts/eval/build_rag_eval_dataset.py --per-source 4 --version v2

# 5. A/B эксперимент с другим prefix-var:
python3 scripts/build_index.py --no-with-heading-prefix --collection nefteboros_corpus_no_prefix
NEFTEBOROS_RAG_COLLECTION=nefteboros_corpus_no_prefix python3 scripts/eval/eval_rag.py --version v1
```

---

## 10. Время и ресурсы

**Период:** 2026-05-06 — 2026-05-07 (примерно 1 день полной работы)

**Прогон одного eval'а:**
- bi-encoder (config=bi): ~3-5 минут на 95 Q (CPU/MPS)
- bi+topic-boost / topic-filter / doc-type-boost: ~10 мин (LLM call ×95 ≈ 8 мин + retrieval 2 мин)

**Сборка всей инфры:**
- 802 чанка через kimi-k2p6 для tagger: ~18 мин (concurrency=20)
- 95-Q датасет через kimi-k2p6: ~2 минуты
- vectorstore рестроится при изменении prefix logic — каждый раз ~12 мин на GPU (или 1.5-2 ч на CPU)

**Стоимость экспериментов:** ~$0 (всё через HydraGPT с unlimited тарифом)
