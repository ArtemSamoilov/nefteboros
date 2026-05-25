#!/usr/bin/env bash
# Hybrid (sparse+dense) retrieval — сборка v2-индекса + before/after eval на 95-Q.
# См. ADR-0027. Запускать на машине с GPU: на MPS-Mac пересборка индекса ~3-4ч,
# на NVIDIA ~12 мин.
#
# ПРЕДУСЛОВИЯ (подготовить ДО запуска):
#   1) data/chunks/*.jsonl присутствуют — 25 файлов / 802 чанка.
#      ВНИМАНИЕ: data/ в .gitignore, чанков НЕТ в репо. Если их нет на этой
#      машине — перенеси каталог data/chunks/ с dev-машины (~11 МБ).
#   2) Активный venv с зависимостями:
#        pip install -e . -r requirements-domain.txt
#      (добавлены rank_bm25, pymorphy3, pymorphy3-dicts-ru — см. requirements-domain.txt)
#   3) torch видит CUDA: python -c "import torch; print(torch.cuda.is_available())"
#      Иначе сборка индекса пойдёт на CPU и будет медленной.
#
# Прогресс виден в реальном времени (tqdm-бары build_index + per-question лог eval).
set -euo pipefail
cd "$(dirname "$0")/../.."
COMMIT=$(git rev-parse --short HEAD)
D=$(date +%F)

echo "==> Шаг 0: проверка корпуса"
N=$(cat data/chunks/*.jsonl 2>/dev/null | wc -l | tr -d ' ' || echo 0)
echo "    чанков в data/chunks: ${N} (ожидается 802)"
if [ "${N}" -lt 1 ]; then
  echo "ОШИБКА: data/chunks/*.jsonl не найдены (data/ gitignored). Перенеси их с dev-машины." >&2
  exit 1
fi

echo "==> Шаг 1: сборка v2 heading-prefix коллекции (nefteboros_corpus_v2_heading)"
echo "           идемпотентно: если коллекция уже собрана — пропустит. ~12 мин на GPU."
python scripts/build_index.py

echo "==> Шаг 2: BASELINE — dense@v2 (ожидается chunk_hit@5 ~0.779 = production)"
python scripts/eval/eval_rag.py --version v1 --config bi \
  --out "metrics/runs/${D}_rag_dense_v2_${COMMIT}.json"

echo "==> Шаг 3: HYBRID — RRF (default fusion)"
python scripts/eval/eval_rag.py --version v1 --config bi+hybrid \
  --out "metrics/runs/${D}_rag_hybrid_rrf_${COMMIT}.json"

echo "==> Шаг 4: HYBRID — weighted (аблация fusion)"
python scripts/eval/eval_rag.py --version v1 --config bi+hybrid-weighted \
  --out "metrics/runs/${D}_rag_hybrid_weighted_${COMMIT}.json"

echo "==> Шаг 4b: HYBRID — auto (роутинг RU→hybrid / EN→baseline) — целевой prod-режим"
python scripts/eval/eval_rag.py --version v1 --config bi+hybrid-auto \
  --out "metrics/runs/${D}_rag_hybrid_auto_${COMMIT}.json"

echo "==> Шаг 5: failure analysis (CHUNK_HIT / SAME_DOC_MISS / CROSS_DOC_MISS)"
python scripts/eval/analyze_rag_failures.py --version v1

echo ""
echo "==> ГОТОВО. JSON-метрики: metrics/runs/${D}_rag_*_${COMMIT}.json"
echo "    Сравни overall.chunk_hit@5: dense_v2 (baseline) vs hybrid_rrf vs hybrid_weighted."
