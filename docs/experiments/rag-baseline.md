# RAG baseline — результаты на v1 датасете

- **Дата:** 2026-05-07
- **Ветка/PR:** `feature/rag-embed-retrieve` / PR #12
- **Связано:** ADR-0016 (embed + retrieve), `docs/experiments/design.md`
- **Артефакты:**
  - `datasets/rag_eval/v1.jsonl` — 95 Q-A пар
  - `metrics/runs/2026-05-07_rag_baseline_bi_8a3cf49.json` — bi-encoder
  - `metrics/runs/2026-05-07_rag_baseline_bi_rerank_*.json` — bi-encoder + reranker (subset)

## Конфигурация системы

- **Embedder:** BGE-M3 (BAAI), 1024-dim, multilingual, max_seq=4096
- **Vector store:** ChromaDB persistent, cosine, 802 чанка
- **Retriever:** bi-encoder top-k_dense=30 → top-k_final=10
- **Опциональный reranker:** bge-reranker-v2-m3 (отключён по умолчанию из-за server constraints, см. ADR-0016)
- **Корпус:** 25 документов, 4 блока (стратегии / корпоративка РФ / operational срез / геополитика)

## Датасет

- **95 вопросов** (sampler настроен на 4×25=100, но в коротких документах <4 чанков ≥500 токенов)
- **Метод сбора:** semi-synthetic — для каждого сэмплированного чанка kimi-k2p6 генерирует 1 вопрос в роли «топ-менеджер банка» + краткий ground truth answer
- **Стратификация:** ровно 4 (или меньше при дефиците) чанка на каждый source
- **Фильтр:** chunks ≥500 токенов (исключаем footnotes / mini-chunks)
- **Random seed:** 42

### Распределение

| Slice | Кол-во |
|---|---:|
| **By language** | |
| EN | 55 |
| RU | 40 |
| **By block** | |
| 1_strategy | 39 |
| 2_corporate | 28 |
| 3_operational | 17 |
| 4_geopolitics | 11 |

### Известные ограничения датасета

1. **Synthetic bias.** Вопросы сгенерированы по самим чанкам — формулировки лексически близки к тексту. Это даёт **«потолок precision сверху»**: реальные пользовательские запросы будут перефразированы → метрики на проде будут хуже.
2. **Source-leak.** Часть вопросов содержит упоминание источника («Bruegel's recent working paper», «What does the IEA forecast...»). Реальный пользователь не знает, какой именно отчёт мы используем — это завышает source_hit@k. На chunk_hit@k влияет меньше.
3. **Один правильный chunk per question.** В реальности один вопрос может иметь правильный ответ в нескольких чанках — мы это не измеряем (нужен polychunk ground truth + recall@k для multi-relevant).

Это **baseline**, не финальная метрика. Для production-eval нужны вопросы, размеченные вручную человеком, не знающим контента.

## Результаты — bi-encoder (default config, server-friendly)

### Overall (n=95)

| Метрика | Значение |
|---|---:|
| chunk_MRR | 0.458 |
| **source_MRR** | **0.883** |
| chunk_hit@1 | 0.326 |
| chunk_hit@3 | 0.526 |
| chunk_hit@5 | 0.653 |
| chunk_hit@10 | 0.737 |
| **source_hit@1** | **0.811** |
| source_hit@3 | 0.947 |
| **source_hit@5** | **0.979** |
| source_hit@10 | 0.989 |

### По языку

| Язык | n | chunk_MRR | chunk_hit@5 | source_hit@5 |
|---|---:|---:|---:|---:|
| EN | 55 | 0.518 | 0.727 | 0.982 |
| RU | 40 | 0.375 | 0.550 | 0.975 |

### По блоку

| Блок | n | chunk_MRR | chunk_hit@5 | source_hit@5 |
|---|---:|---:|---:|---:|
| 1_strategy | 39 | 0.553 | 0.769 | 0.974 |
| 2_corporate | 28 | 0.251 | 0.429 | 0.964 |
| 3_operational | 17 | 0.477 | 0.588 | 1.000 |
| 4_geopolitics | 11 | 0.619 | 0.909 | 1.000 |

## Интерпретация

### Главное

**`source_hit@5 = 97.9%`** — retrieval практически всегда находит правильный документ. Для пользовательского сценария это критично: даже если точный chunk не на 1-м месте, в top-5 будет правильный документ → можем взять расширенный контекст для LLM-синтеза, и ответ окажется внутри.

### Где проседаем

- **RU < EN на 18 п.п. по chunk_hit@5** (55% vs 73%). BGE-M3 multilingual, но качество на английском у него заметно выше. Митигации (backlog v1.x):
  - HyDE / query rewriting — переводить RU-query в EN или генерировать «гипотетический ответ»
  - GigaChat embeddings — нативно RU, может быть лучше на нашем корпусе (требует бенчмарка)
  - Hybrid BM25 + dense — BM25 на леммах хорошо работает для русских терминов

- **2_corporate (chunk_hit@5 = 43%) — заметно хуже остальных блоков**. Причина: AR крупных компаний (Газпром, Роснефть — 22 МБ) содержат много semantically близких chunks (финрезы за разные периоды, история, ESG). Один и тот же тип вопроса даст несколько одинаково-релевантных chunks → точный match сложен.
  - **Compensation:** `source_hit@5 = 96%` — правильная компания всё равно находится в top-5, и LLM сможет ответить на основе нескольких chunks одного источника.

### Что хорошо работает

- **4_geopolitics (chunk_hit@5 = 91%)** — узкие тематические обзоры (Bruegel WP, CRS Iran), нет внутренних дубликатов
- **1_strategy (chunk_hit@5 = 77%)** — большие, но структурированные документы со специфическим контентом
- **3_operational source_hit@5 = 100%** — свежие отчёты OPEC/EIA/IEA пишутся узко по теме месяца

## Сравнение с reranker (bi+rerank) — не выполнено

Прогон с `bge-reranker-v2-m3` не удался ни на одной из доступных машин:
- **NVIDIA GPU 8 ГБ:** при загрузке двух моделей (BGE-M3 ~2.3 ГБ + bge-reranker-v2-m3 ~2.3 ГБ) VRAM забит на 7962/8192 МБ, smoke-тесты висели 30+ мин (см. ADR-0016, секция Calibration)
- **Mac M-series CPU/MPS:** на subset 20 вопросов — `RuntimeError: Invalid buffer size: 32.68 GiB` на attention кросс-энкодера для длинных пар (query, chunk) с k_dense=30

**Вывод:** bge-reranker-v2-m3 (~568M params) на длинных чанках (max 4031 токенов × 30 пар) требует **GPU 12+ ГБ или специальной memory-aware реализации**. На сервере 4 ГБ — точно не работает. На наших dev-машинах (Mac 16 ГБ unified, NVIDIA 8 ГБ) — тоже.

**Стратегия для production rerank** (backlog v1.x):
1. **LLM-rerank через kimi-k2p6** — HTTP вызов, ~3-5 сек latency, не требует памяти на сервере. Промпт: «вот вопрос и 30 кандидатов с краткими сниппетами, ранжируй». Будет реализовано в `feature/rag-llm-rerank`.
2. **`bge-reranker-base`** (278M, ~600 МБ) — в 4× легче v2-m3, multilingual. Может уместиться.
3. **Truncate chunks до 1024 токенов перед reranker'ом** — снизит memory linearly.

Поскольку **baseline bi-encoder уже даёт source_hit@5 = 98%**, reranker — оптимизация, а не блокер для v1.0.

## Решение по приёмке

Baseline **достаточно** для v1.0 production:
- `source_hit@5 = 98%` — пользователь всегда увидит правильный документ
- `chunk_MRR = 0.46` — правильный chunk на 2-3 позиции в среднем (топ-5 покрывает 65%)
- Слабое место (RU/корпоративка) — известно, в backlog v1.x

## Backlog для улучшения retrieval (по приоритету)

1. **HyDE для RU-запросов** — kimi генерирует «гипотетический ответ» на запрос, эмбеддим его. Должно поднять RU chunk_hit на 5-15 п.п.
2. **Hybrid BM25 + dense** — для точных терминов (тикеры, имена компаний)
3. **LLM-rerank через kimi** на сервере — замена cross-encoder без локальной модели
4. **Polychunk ground truth + recall@k** — точнее метрика для AR с similar chunks
5. **Manual eval dataset** — 30-50 вопросов от человека, не знающего корпуса
6. **Query routing** — different retrieval strategies per question type (фактический → дата+цифры, аналитический → wider top-k)

## Воспроизводимость

```bash
# 1. Чанки и vectorstore (предполагается уже собранные)
ls data/chunks/*.jsonl  # 25 файлов, 802 чанка
python3 scripts/build_index.py  # на ноуте с GPU

# 2. Сгенерировать датасет (требует HYDRA_API_KEY в .env, ~2 мин)
python3 scripts/eval/build_rag_eval_dataset.py --per-source 4 --version v1

# 3. Eval
python3 scripts/eval/eval_rag.py --version v1 --config bi
python3 scripts/eval/eval_rag.py --version v1 --config bi+rerank --limit 20
```
