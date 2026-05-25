# RAG — Hybrid sparse+dense retrieval + language-routing

> Сводный документ по апдейту RAG-ретривера: что изменилось, как пользоваться, как шёл эксперимент (по факту, с поправками) и текущее состояние. Глубокие разборы — в ADR-0027 и `experiments/rag-hybrid-experiments.md`.

- **Статус:** в `main` (PR #79, commit `82ee133`). Прод-дефолт **выключен** — поведение не изменилось.
- **Связано:** ADR-0027 (решения), `docs/experiments/rag-hybrid-experiments.md` (замеры), ADR-0016 (предшественник, dense-only).

---

## 1. Что изменилось

Ретривер был чисто dense (BGE-M3 → ChromaDB cosine). Добавлена **лексическая (sparse) компонента** и слияние с dense:

```
query ─┬─► dense  (BGE-M3 → Chroma, top-k_dense)        ─┐
       └─► sparse (BM25 over 802 чанков, top-k_sparse)  ─┴─► fusion (RRF) ─► top-k_final
```

**Зачем.** Главный failure mode dense-baseline'а — `SAME_DOC_MISS`: правильный документ найден (`source_hit@5 = 0.989`), но внутри него выше ранжируется не тот фрагмент. Лексический матч точных термов (тикеры, имена компаний, числа, заголовки) различает соседние чанки одного документа лучше семантики. Результат: `SAME_DOC_MISS` 21.1% → 13.7% (−35%).

---

## 2. Как пользоваться

### ENV

| Переменная | Default | Значения |
|---|---|---|
| `NEFTEBOROS_HYBRID` | `off` | `off` \| `on` (всегда hybrid) \| **`auto`** (роутинг по языку) |
| `NEFTEBOROS_HYBRID_FUSION` | `rrf` | `rrf` \| `weighted` |
| `NEFTEBOROS_HYBRID_RRF_K` | `60` | сглаживающая константа RRF |
| `NEFTEBOROS_HYBRID_ALPHA` | `0.5` | вес dense в weighted-слиянии |
| `NEFTEBOROS_RETRIEVAL_K_SPARSE` | `30` | глубина BM25-списка |
| `NEFTEBOROS_SPARSE_CACHE_DIR` | `data/sparse_index` | дисковый кэш токенов |

**Режим `auto`** = роутинг по языку запроса (детектор `nefteboros/search/lang.py`): **RU → hybrid, EN → dense baseline**. Это целевой прод-режим (обоснование — §4).

### API

```python
from nefteboros.rag.retriever import Retriever

r = Retriever()
r.retrieve("прогноз Brent на 2026", hybrid="auto")   # роутинг по языку
r.retrieve("OPEC quota Q2", hybrid="on")             # всегда hybrid
r.retrieve("...", hybrid=False)                       # явный dense (eval/тесты)
```

`hybrid` принимает `bool` (явное вкл/выкл, приоритет) или `str`-режим (`off`/`on`/`auto`).

---

## 3. Результаты (95-Q `datasets/rag_eval/v1.jsonl`, v2-prefix, synthetic)

| Конфиг | chunk_hit@5 | chunk_hit@1 | source_hit@5 |
|---|---:|---:|---:|
| dense (baseline) | 0.779 | 0.347 | 0.989 |
| hybrid RRF (global) | 0.832 (+5.3) | 0.421 | 0.968 (−2.1) |
| hybrid weighted (global) | 0.863 (+8.4) | 0.516 | 0.979 |
| **hybrid auto (routing)** | **0.874** (+9.5) | 0.389 | **0.989** (=baseline) |

По языку (chunk_hit@5): RU 0.675 → 0.900 (**+22.5**), EN 0.855 → 0.782 (**−7.3** на global). Цена hybrid (регрессия + просадка recall документа) — **целиком EN**; роутинг (EN→dense) её устраняет. Детекция языка: 22/22 на коротких/тикерных запросах, 0 ложных срабатываний.

---

## 4. Как шёл эксперимент (по факту, с поправками)

Документируем честно — путь был не прямым, и поправки важнее глянцевого итога.

1. **Какая коллекция «production».** Живая локальная коллекция `nefteboros_corpus_v1` оказалась **raw-эмбеддингами (chunk_hit@5 = 0.653)**, а не v2-prefix (0.779), как предполагалось. `nefteboros_corpus_v2_heading` была пуста. Без этой проверки сравнение шло бы с 0.653 и завысило бы выигрыш на ~12 п.п. v2-prefix пересобран на GPU (RTX 2070, ~12 мин) → честный baseline 0.779.

2. **Гипотеза «на v2 прирост меньше» — опровергнута.** Ожидали diminishing returns (heading-prefix уже бьёт по SAME_DOC_MISS, как и BM25). По факту на corporate hybrid дал **+28.6 п.п. на v2** против +21.4 на v1 — БОЛЬШЕ. Prefix и BM25 — **дополняющие** сигналы (prefix = документный контекст, BM25 = лексический матч внутри документа), а не дублирующие.

3. **+5.3 overall — это перераспределение, а не равномерный подъём.** RU/corporate сильно вверх, EN/strategy/operational вниз, `source@5` −2.1. Принцип: **fusion помогает там, где dense слаб, и вредит там, где dense уже у потолка** (на v1 регрессии не было — dense был слаб везде).

4. **Поправка по фреймингу (ключевая).** Сначала привязали решение к доле RU/EN в трафике. Это неверно: **роутинг ≥ baseline при ЛЮБОЙ доле EN**, потому что EN-ветка = dense baseline (нулевая просадка), а весь выигрыш — на RU. Доля языка определяет лишь РАЗМЕР приза, не «включать ли». Реальные гейты — (1) надёжность детекции, (2) подтверждение на живых данных. Роутинг `auto` (0.874, `source@5` восстановлен до 0.989) строго доминирует глобальный hybrid.

5. **weighted > RRF на synthetic — это bias, не сигнал.** Вопросы сгенерены LLM по тексту чанков → BM25-скоры пиковые → weighted эксплуатирует магнитуду. На реальных запросах преимущество схлопнется. Поэтому **RRF — default**, а weighted держим для аблации.

6. **Ловушка датасета.** Все цифры — на synthetic 95-Q с лексическим bias → это **верхняя оценка**. Реальный профиль подтвердит только human-сет (§6).

---

## 5. Зависимости и ресурсы

- `rank_bm25`, `pymorphy3`, `pymorphy3-dicts-ru` (в `requirements-domain.txt`; опциональны для dense-only прода — при `NEFTEBOROS_HYBRID=off` не импортируются).
- BM25-индекс **in-memory ~13 МБ**, без второй модели — помещается в 4 ГБ сервера (в отличие от cross-encoder reranker'а, отключённого по памяти, ADR-0016).
- RU-токенизация: лемматизация pymorphy3 (`нефти→нефть`). Токенизированный корпус кэшируется (`data/sparse_index/`), лемматизация прогоняется один раз; кэш инвалидируется по `TOKENIZER_VERSION` + сигнатуре корпуса.

---

## 6. Эксплуатация и eval

```bash
# eval на 95-Q (нужен собранный индекс nefteboros_corpus_v2_heading):
python scripts/eval/eval_rag.py --version v1 --config bi              # baseline
python scripts/eval/eval_rag.py --version v1 --config bi+hybrid       # global RRF
python scripts/eval/eval_rag.py --version v1 --config bi+hybrid-auto  # routing (целевой)

# полный before/after + пересборка индекса (на машине с GPU):
bash scripts/eval/run_hybrid_eval.sh

# сборка/пересборка v2-индекса:
python scripts/build_index.py            # nefteboros_corpus_v2_heading, with-heading-prefix
```

Восстановленный v2-store лежит в `data/vectorstore_v2_heading/` (используется через `NEFTEBOROS_RAG_VECTORSTORE_PATH`). RAG-eval (`config=bi*`) не зовёт GigaChat → работает и на Python 3.14.

---

## 7. Текущее состояние и следующий шаг

- В проде **`NEFTEBOROS_HYBRID=off`** — ничего не изменилось, риска нет.
- **Гейт смены дефолта на `auto`:** прогон на **~25 живых RU-вопросах** (пишут люди, НЕ LLM-перефраз чанков — иначе тот же BM25-bias, проверка пустая). Harness готов: положить `datasets/rag_eval/human_ru.jsonl` (схема `question / expected_chunk_id / expected_source_id / language / block`) → `eval_rag.py --version human_ru --config bi` vs `bi+hybrid-auto`.
- **Правило решения:** RU-выигрыш усохнет вдвое (+11 п.п.) → всё равно включаем (`auto` ≥ baseline); схлопнется → обсуждаем.

---

## 8. Глубже

- `docs/adr/0027-hybrid-retrieval.md` — развилки (BM25 vs BGE-M3 sparse, RRF vs weighted, RU-токенизация), последствия, конфигурация.
- `docs/experiments/rag-hybrid-experiments.md` — все замеры, слайсы, failure-breakdown, `source@5`-by-lang, разбор synthetic-bias.
- `docs/changelog/2026-05-24-rag-hybrid-search.md` — PR-сводка.
- Код: `nefteboros/rag/{text_norm,sparse_index,fusion}.py`, `retriever.py`; тесты `tests/test_rag_hybrid.py` (16).
