# Changelog: rag-extract — PDF → Markdown через Marker

- **Дата:** 2026-05-05
- **PR:** `feature/rag-extract`
- **ADR:** [docs/adr/0010-pdf-to-markdown-marker.md](../adr/0010-pdf-to-markdown-marker.md)
- **Этап:** 1 из 3 в RAG-pipeline (после A → B `feature/rag-chunk` → C `feature/rag-embed-retrieve`).

## Задача

Превратить корпус из 25 PDF в layout-aware Markdown — структурированный текст с сохранёнными таблицами и страничными маркерами, пригодный для chunking в PR B.

## Почему Marker, а не альтернативы

См. ADR-0010. Коротко:
- ~50% страниц нашего корпуса — таблицы. Marker даёт **best-in-class** качество таблиц (Surya OCR + table-structure модели).
- Артём подтвердил: для тестового задания GPL-3 не блокирует.
- На GPU 8 ГБ — ~1 сек/стр × 2200 стр ≈ **30 минут** конвертации.
- В будущем prod-Сбере: возможна замена на Docling (MIT) — изоляция через `nefteboros.rag.convert` позволяет.

## Что сделано

### Код

- `nefteboros/rag/convert.py` — обёртка над Marker:
  - `get_default_converter()` — lazy-load (3-5 ГБ моделей загружаются один раз)
  - `convert_pdf(pdf_path, md_path, converter=None)` — конвертирует один PDF, возвращает `ConversionResult` (страницы, время, размер MD)
  - Конфиг: `paginate_output=True` → page-маркеры `{N}` сохраняются для будущего chunking
- `scripts/convert_corpus.py` — идемпотентный CLI:
  - Читает 25 документов из `data/metadata/manifest.yml`
  - Для каждого: `data/corpus/<file>` → `data/markdown/<source_id>.md`
  - Поддерживает `--only <substr>[,<substr>]` (фильтр), `--force` (перезаписать), `--check` (без вызова Marker)
  - Один Marker-инстанс переиспользуется для всех документов (3-5 ГБ моделей грузятся раз)

### Зависимости

- `requirements-conversion.txt` — **новый отдельный** файл с `marker-pdf>=1.5.0`. На сервере деплоя **не устанавливается** (рантайм агент работает с собранной Chroma).
- `requirements.txt` и `requirements-domain.txt` **не изменены**.
- PyTorch ставится отдельно под GPU backend (CUDA / MPS / CPU) — см. ADR-0010 и комментарии в `requirements-conversion.txt`.

### Структура каталогов

- `data/markdown/` — новый, gitignored (как `data/corpus/`); только `.gitkeep` в репо.
- `.gitignore` — добавлено `/data/markdown/*` + exception для `.gitkeep`.

### Документация

- `docs/adr/0010-pdf-to-markdown-marker.md` — обоснование выбора Marker, конфигурация, что не в этом PR.
- `nefteboros/rag/__init__.py` — обновлён план модулей (convert.py добавлен, parser.py убран).
- `docs/changelog/2026-05-05-rag-extract.md` — этот файл.

## Что НЕ в этом PR

- Чанкинг MD → **PR B** `feature/rag-chunk` + ADR-0011 (heading-aware splitter, спецлогика для больших таблиц, source/section/topic-tagging со словарём)
- Эмбеддинги (BGE-M3) + Chroma + retrieval + bge-reranker → **PR C** `feature/rag-embed-retrieve` + ADR-0012
- Eval RAG-метрик → отдельный PR `feature/eval-rag` после C

## Тесты

- `python3 -c "import ast; ast.parse(...)"` — AST OK для convert.py и convert_corpus.py
- `python3 scripts/convert_corpus.py --check` — манифест читается, 25 документов в очереди, Marker не загружается (контракт `--check` соблюдён)
- **Real-конвертация на CPU/GPU локально не запускалась** — у меня нет GPU, и Marker-модели ~3-5 ГБ; запускает Артём на своём 24/8GB-ноуте после merge.

## Как запустить (Артёму на 24GB/8GB-GPU ноуте)

```bash
git pull origin main

python3 -m venv .venv-conversion && source .venv-conversion/bin/activate
pip install -r requirements-conversion.txt
# PyTorch под backend ноута — выбери одно:
pip install torch --index-url https://download.pytorch.org/whl/cu124   # NVIDIA CUDA
pip install torch                                                      # Apple Silicon (MPS) или CPU
pip install torch --index-url https://download.pytorch.org/whl/cpu     # CPU only

# Smoke-test на 3 разнотипных PDF (RU government / EN табличный / EN academic):
python scripts/convert_corpus.py --only gov_rf_energostrategy_2050,opec_asb_2024,bruegel_wp

# Если всё ок — полная конвертация (~30 мин на GPU):
python scripts/convert_corpus.py

# Проверь визуально 3 sample MD:
ls -la data/markdown/
head -100 data/markdown/gov_rf_energostrategy_2050.md
head -100 data/markdown/opec_asb_2024.md         # таблицы
head -100 data/markdown/bruegel_wp_2025-32_oil_sanctions.md
```

## Файлы

**Добавлено:**
- `nefteboros/rag/convert.py`
- `scripts/convert_corpus.py`
- `requirements-conversion.txt`
- `docs/adr/0010-pdf-to-markdown-marker.md`
- `docs/changelog/2026-05-05-rag-extract.md`
- `data/markdown/.gitkeep`

**Изменено:**
- `.gitignore` — добавлено `/data/markdown/*` + exception
- `nefteboros/rag/__init__.py` — обновлён план модулей под Marker pipeline
