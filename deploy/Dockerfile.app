# nefteboros — application Dockerfile (multi-stage)
#
# Stage 1 (builder): системные зависимости + pip install в venv.
# Stage 2 (runtime): минимальный slim + копия venv + код.
#
# Build:    docker build -f deploy/Dockerfile.app -t nefteboros:dev .
# Run:      docker compose -f deploy/docker-compose.yml up -d
# Logs:     docker compose -f deploy/docker-compose.yml logs -f web
#
# Размер: ~2 GB. Без CUDA-пакетов nvidia* (torch CPU-only) и без playwright
# browser (server.py не использует browser tools для нашего scope F-track).

FROM python:3.12-slim AS builder

WORKDIR /build

# Build deps: компиляция wheels (numpy/sentence-transformers/PyMuPDF), git для VCS deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    git \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# venv в /opt/venv — переносим целиком в runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 1. Torch CPU-only ПЕРЕД остальными — иначе sentence-transformers тащит
#    full CUDA build (torch + nvidia-cublas + cudnn + triton + ...) на 3-4 GB.
#    На сервере без GPU это бесполезно и в прошлом build'е вызвало OOM при
#    `exporting to image` слое.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

# 2. Ouroboros core. Playwright/playwright-stealth исключены — browser tools
#    не задействованы в WS chat / agent loop через neftegaz_analyst skill.
COPY requirements.txt /build/
RUN grep -vE "^(playwright|playwright-stealth)" requirements.txt > /tmp/req-core.txt && \
    pip install -r /tmp/req-core.txt

# 3. Domain (langchain, langfuse, sentence-transformers, statsmodels, ...).
COPY requirements-domain.txt /build/
RUN pip install -r requirements-domain.txt

# Playwright Chromium и BGE-M3 НЕ предзагружены — для F-track verify (chat
# через WS, observability) не критично. Подключим если потребуется в финале.

# ============================================================
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime deps:
#   libgomp1   — OpenMP для numpy/statsmodels/torch.
#   git        — dulwich/ouroboros git operations.
#   curl       — healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# venv из builder.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Код проекта. .dockerignore исключает .venv/, data/, .git/, worktrees.
COPY . /app

# Server defaults (можно переопределить в compose).
# Endpoint: server.py читает OUROBOROS_SERVER_PORT (default 8765 → меняем на
# 8000 для соответствия compose), OUROBOROS_DATA_DIR — drive root.
ENV OUROBOROS_SERVER_PORT=8000 \
    OUROBOROS_DATA_DIR=/app/data \
    OBSERVABILITY_RUN_DIR=/app/data/observability

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://localhost:${OUROBOROS_SERVER_PORT}/api/health" || exit 1

ENTRYPOINT ["python", "server.py"]
