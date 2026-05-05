# ADR-0010 — PDF → Markdown через Marker

- **Дата:** 2026-05-05
- **Статус:** Принято
- **Контекст:** PR `feature/rag-extract` (этап 1 из 3 в RAG-pipeline)
- **Связано:** ADR-0009 (corpus strategy), будущие ADR-0011 (chunking + tagging), ADR-0012 (embed + retrieve)

## Контекст

Корпус из 25 PDF (~135 МБ, ~2200 страниц, см. ADR-0009) нужно превратить в **структурированный текст**, пригодный для chunking и эмбеддингов.

Сложности именно для нашего корпуса:
- ~50% страниц — таблицы (OPEC ASB, EI Stat Review, MOMR, отчёты компаний). Сырая text-extraction убивает структуру таблиц → retrieval даёт мусор на табличных вопросах.
- Документы на двух языках (RU + EN) — нужен Unicode-aware парсер.
- Сложные layout: 2 колонки (МЭА reports), сноски, headers/footers, диаграммы с подписями.
- Корпоративные годовые отчёты (Газпром AR, Новатэк AR) — фирменный графический дизайн с нестандартным layout.

«Сырой PyMuPDF text dump» работает на простых документах, но рассыпается на нашем корпусе. Нужен **layout-aware конвертер**.

## Решение

Используем **[Marker](https://github.com/datalab-to/marker)** (Datalab) для конвертации PDF → Markdown.

Конкретно:
- Главный entry point — `nefteboros.rag.convert.convert_pdf()`
- CLI — `python scripts/convert_corpus.py [--only <substr>] [--force]`
- Вход: `data/corpus/*.pdf` (по списку из `data/metadata/manifest.yml`)
- Выход: `data/markdown/<source_id>.md` (gitignored, как PDF)
- Marker запускается с `paginate_output=True` → page-маркеры `{N}` сохраняются в MD для будущего chunking metadata
- ML-модели Marker (~3-5 ГБ) — **отдельный requirements-файл** `requirements-conversion.txt`. На сервере деплоя они **не нужны** (рантайм агент использует уже собранные эмбеддинги).

## Аргументация

### Почему Marker, а не альтернативы

Сравнение топ-3 на момент решения (2026-05):

| Конвертер | Качество таблиц | Скорость GPU 8 GB | Лицензия | Замечания |
|---|---|---|---|---|
| **Marker** (Datalab) | **best-in-class** | ~1 сек/стр | GPL-3 | Surya OCR + table-structure модели + равняется человеку на сложных таблицах |
| Docling (IBM) | хорошее | ~0.5 сек/стр | MIT | Быстрее, но на сложных нестандартных таблицах теряет структуру |
| MinerU (Shanghai AI Lab) | отличное | ~1 сек/стр | AGPL | Хорош на научных PDF, чуть слабее на корпоративных layout |
| PyMuPDF4LLM | посредственное | мгновенно | AGPL | Быстро, но качество близко к сырому extraction |

Выбран **Marker** по приоритету **качества таблиц** — у нас половина корпуса (OPEC ASB, EI Stat Review, AR компаний) это таблицы. Скорость не критична: конвертация — **one-time offline-этап** на машине Артёма с GPU, потом MD кладутся в репо/sync на сервер.

### Почему лицензия GPL-3 не блокирует

Это **тестовое задание**, не коммерческий продукт под Сбером. GPL-3 заражает только **производное ПО**, но мы используем Marker как CLI/библиотеку для генерации **данных** (MD-файлы) — на наш собственный код GPL не распространяется. Артём подтвердил, что для тестового санкционных ограничений нет.

(В будущем production-Сбер варианте — переехать на Docling с MIT.)

### Почему результаты MD сохраняем on-disk (gitignored)

- **Reproducibility и debug.** Можно открыть файл и увидеть что агент видит. При chunking-проблемах — критично.
- **Идемпотентность.** Конвертация ~30 минут на GPU — не повторяем при каждом chunking-эксперименте.
- **gitignored, как PDF** — derived data, ~30-50 МБ, регенерируется по `python scripts/convert_corpus.py`.
- **На сервере не нужны.** Мы пушим уже **собранную Chroma** (после PR C), MD на сервере — лишний шум.

### Почему `paginate_output=True`

Marker по умолчанию не маркирует страницы в MD. С флагом — добавляет `{PAGE-N}` разделители. Это **бесплатно** на этапе конвертации, но критично для PR B (chunking) — позволит писать в metadata чанка реальный page number из исходного PDF (для citations в ответах агента вида «по данным OPEC MOMR Apr 2026, стр. 47»).

## Конфигурация

### Зависимости

Новый файл **`requirements-conversion.txt`**:
```
marker-pdf>=1.5.0
torch>=2.4.0
```

PyTorch ставится **отдельно** под backend (CUDA / MPS / CPU). На GPU 8 ГБ NVIDIA: `pip install torch --index-url https://download.pytorch.org/whl/cu124`. На Apple Silicon — обычный `pip install torch` (MPS поддержка нативно).

В `requirements.txt` и `requirements-domain.txt` Marker **не добавляется**: на сервере его не нужно ставить.

### Layout каталогов

```
data/
  corpus/        # PDF, gitignored (24 + 1 manual)
  markdown/      # MD, gitignored — выход convert_corpus.py
  metadata/      # manifest.yml, в репо
  vectorstore/   # Chroma persist, gitignored — будет в PR C
```

### Запуск

```bash
# Установка (один раз, на машине с GPU):
pip install -r requirements-conversion.txt
pip install torch --index-url https://download.pytorch.org/whl/cu124  # CUDA
# или
pip install torch  # Apple Silicon / CPU

# Конвертация всего корпуса (~30 мин на GPU 8GB):
python scripts/convert_corpus.py

# Перезапустить только для одного документа:
python scripts/convert_corpus.py --only opec_asb --force

# Проверить что MD получился (без вызова Marker):
python scripts/convert_corpus.py --check
```

## Последствия

**Плюсы:**
- Качественные MD с сохранённой табличной структурой → лучшая база для PR B (chunking)
- Page-markers сохранены → точные citations в ответах агента
- Конвертация — изолированный модуль, можно заменить движок (Docling/MinerU) без правки кода chunking
- Опциональная зависимость → продакшен-сервер легче

**Минусы:**
- ~30 мин конвертации (one-time)
- ML-модели ~3-5 ГБ диска
- Нужна машина с GPU для разумной скорости (на CPU ~6 часов)
- GPL-3 — для будущего prod-Сбера потребуется замена

**Митигации:**
- Конвертацию делает Артём на своём 24/8GB-ноуте, MD-файлы потом sync на любой развёрток
- Если Marker провалится на конкретных PDF — добавим Docling как fallback в одном из следующих PR

## Что НЕ в этом PR

- Чанкинг MD на чанки → PR B `feature/rag-chunk` + ADR-0011
- Спецлогика для больших таблиц (не резать пополам, дублировать header) → PR B
- Topic-tagging и закрытый словарь → PR B (словарь финализируем после визуальной проверки MD)
- Эмбеддинги (BGE-M3) → PR C `feature/rag-embed-retrieve`
- Chroma persist → PR C
- Retrieval + reranker (bge-reranker-v2-m3) → PR C
- Tool wrapper для агента → PR C
- Eval RAG-метрик → отдельный PR `feature/eval-rag`

## Альтернативы рассмотренные

- **PyMuPDF text dump + RecursiveCharacterTextSplitter** — отвергнуто: убивает таблицы, плохо на двухколоночных layout.
- **PyMuPDF4LLM** — отвергнуто: лишь немного лучше сырого PyMuPDF, для табличного корпуса недостаточно.
- **Docling** — рассматривался как main, но проиграл Marker по качеству таблиц на нашем корпусе. Останется в backlog как fallback при проблемах с Marker и как замена для prod-Сбера (MIT-лицензия).
- **MinerU** — хорошая альтернатива, но AGPL ещё сложнее GPL для prod, и нет преимуществ перед Marker на корпоративных layout.

## Ссылки

- [Marker (Datalab)](https://github.com/datalab-to/marker)
- ADR-0009: [docs/adr/0009-corpus-strategy.md](0009-corpus-strategy.md)
