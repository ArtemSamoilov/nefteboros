# RAG embedding prefix — 4 итерации эксперимента

- **Дата:** 2026-05-07
- **Ветка/PR:** `feature/eval-rag-v2`
- **Связано:** ADR-0016 (embed + retrieve), `docs/experiments/rag-baseline.md` (v1 baseline)
- **Артефакты:**
  - `metrics/runs/2026-05-07_rag_baseline_bi_*.json` — по одному JSON на каждую версию
  - `datasets/rag_eval/v1.jsonl` — 95 Q-A пар (semi-synthetic, kimi-k2p6)

## Контекст

Failure analysis на baseline v1 (см. `rag-baseline.md`) показал что **embedding почти всегда находит правильный документ** (cross_doc_miss = 2.1%), но **внутри документа** теряет точный chunk (same_doc_miss = 32.6%). Особенно критично для table_only chunks (chunk_hit@5 = 20%) и корпоративных AR с similar chunks.

Серия экспериментов — добавление context'а в text перед embedding'ом, чтобы помочь BGE-M3 различать чанки внутри одного документа.

## Конфигурации

### v1 — baseline (no prefix)

```
{text}
```

Embedding получает только raw content чанка.

### v2 — simple heading prefix

```
[{source_title}]
{section_path}

{text}
```

Добавлены explicit identifier документа (в скобках) и полный heading-path. section_path как есть из chunker'а — с возможным мусором HTML-тегов от Marker'а.

### v3 — enriched prefix (HTML clean + dedup + first_meaningful_line)

```
[{source_title}]
{cleaned_section_path}
>>> {first_meaningful_content_line}

{text}
```

Дополнительно:
- HTML-теги Marker'а (`<span id="page-N">`) почищены из section_path
- Dedup: если section_path начинается с source_title — отрезаем
- `>>> {first_line}` — первая значимая строка content'а (не таблица, не page-маркер, ≥10 chars). Гипотеза: это часто bold/CAPS sub-section, который chunker не распарсил как H2

### v4 — clean без first_line

```
[{source_title}]
{cleaned_section_path}

{text}
```

Откат `first_meaningful_line` (после регресса в v3 на text_only/table_only). HTML clean + dedup сохранены.

## Результаты

Eval на одном и том же датасете 95 Q (стратифицированный sampling по source).

### Overall

| Метрика | v1 | v2 | v3 | v4 |
|---|---:|---:|---:|---:|
| chunk_hit@1 | 0.326 | 0.347 | **0.421** | 0.358 |
| chunk_hit@3 | 0.526 | **0.674** | 0.611 | 0.642 |
| **chunk_hit@5** | 0.653 | **0.779** | 0.768 | 0.758 |
| chunk_hit@10 | 0.737 | 0.874 | **0.905** | 0.895 |
| chunk_MRR | 0.458 | 0.527 | **0.553** | 0.528 |
| source_hit@5 | 0.979 | 0.989 | 0.989 | 0.989 |

**v2 — победитель по ключевой метрике chunk_hit@5.** Более сложные prefix (v3/v4) — регрессы.

### Failure breakdown

| Категория | v1 | v2 | v3 | v4 |
|---|---:|---:|---:|---:|
| CHUNK_HIT | 65.3% | **77.9%** | 76.8% | 75.8% |
| SAME_DOC_MISS | 32.6% | 21.1% | 22.1% | 23.2% |
| CROSS_DOC_MISS | 2.1% | 1.1% | 1.1% | 1.1% |

### По типу контента (chunk_hit@5)

| Тип | v1 | v2 | v3 | v4 |
|---|---:|---:|---:|---:|
| text_only | 64.9% | **75.7%** | 70.3% | 73.0% |
| with_table | 81.4% | 83.7% | **88.4%** | 86.0% |
| **table_only** | **20.0%** | **66.7%** | 60.0% | 60.0% |

**Table_only — главная победа v2 (+46.7 п.п. над v1).** Heading prefix даёт BGE-M3 семантический контекст для chunks типа `Mexico US 7.92 92.43 ...`.

## Что мы поняли

### v2 → v3: «больше — не лучше»

`>>> first_meaningful_line` помог на with_table (+4.7), но **повредил text_only** (-5.4) и **table_only** (-6.7). Гипотеза:
- Для text_only первая строка часто **общая фраза-вступление** — отвлекает embedder от сути chunk'а
- Для table_only первая строка часто == table caption, **уже** в section_path → дублирование

### v3 → v4: «чистка тоже не лучше»

HTML cleanup `<span id="page-N">` и dedup source_title — **регресс** v4 vs v2 на 2.1 п.п. Контр-интуитивно. Возможные причины:
- HTML-теги в section_path несли **page-сигнал** — embedding ассоциировал чанк с конкретной страницей источника
- Двойное упоминание source_title (в `[...]` brackets и в section_path) **усиливало identity** документа в embedding пространстве

### Главный takeaway

**Меньше manipulations — лучше для BGE-M3.** Простой `[source_title]\n{section_path}\n\n{text}` оказался самым устойчивым. Эвристики (cleanup, first_line) рискуют убрать полезный сигнал.

## Cross-doc miss — нерешённая задача

**Роснефть IFRS vs AR (cash flow query)** упорно промахивается во всех 4 версиях:

```
Q: «Каков чистый денежный поток от операционной деятельности Роснефти за 2024 год?»
expected: rosneft_ifrs_12m_2024 (МСФО)
got top-5: ['rosneft_ar_2024', 'rosneft_ar_2024', 'lukoil_ar_2024', ...]
```

В AR Роснефти есть **выжимка финансов с тем же лексикомом**, embedding не отличает от полной МСФО. Никакой prefix-вариант не решил эту проблему.

**Решение для cross-doc** — другой уровень pipeline:
1. **Topic-tags filter в retrieval** (через query intent classifier). У нас уже есть теги в metadata чанков, но в retrieval не используются.
2. **Document type filter** через explicit `where={"type": "financial_report"}` для финансовых запросов.

Это отдельный backlog — `feature/rag-topic-filter`.

## Решение

**Production default = v2 (simple heading prefix).**

```python
# nefteboros/rag/schema.py
def text_for_embedding(self, *, with_heading_prefix: bool = True) -> str:
    if not with_heading_prefix:
        return self.text
    sp = self.section_path or "(no section)"
    return f"[{self.source_title}]\n{sp}\n\n{self.text}"
```

Production метрики (vs v1 baseline):
- **chunk_hit@5: 0.653 → 0.779 (+12.6 п.п.)**
- **table_only: 20% → 67% (+47 п.п.)** — критическая победа
- source_hit@5: 0.979 → 0.989

После merge нужен **ребилд production vectorstore** с новым default флагом — `python scripts/build_index.py --force` (на сервере или off-server с GPU).

## Следующие шаги (backlog v1.x)

По приоритету ожидаемого прироста:

1. **Topic-tags filter в retrieval** — query classifier через kimi → Chroma `where`. Должен решить cross-doc misses (Роснефть IFRS/AR).
2. **HyDE для RU** — kimi генерирует «гипотетический ответ» → embedding. Может улучшить RU (50-70%) до уровня EN (80%+).
3. **Fix chunker bug** — chunker не отслеживает bold/CAPS sub-headings внутри chunk'а (см. failure analysis: section_path != content в Газпром AR / Роснефть AR). Требует пересборки chunks.
4. **Manual eval dataset** (без semi-synthetic bias). Артём подсветил, что наши метрики — потолок сверху из-за source-leak в формулировках.

## Воспроизводимость

```bash
# 1. Чанки и vectorstore (предполагается данные на месте)
ls data/chunks/*.jsonl | wc -l   # 25
python3 scripts/build_index.py   # default = with_heading_prefix=True

# 2. Eval
python3 scripts/eval/eval_rag.py --version v1

# 3. Failure analysis
python3 scripts/eval/analyze_rag_failures.py --version v1 --dump-misses /tmp/misses.tsv
```

Для repro экспериментов в отдельные коллекции — флаги `--collection <name>` и `--no-with-heading-prefix` в `build_index.py`.
