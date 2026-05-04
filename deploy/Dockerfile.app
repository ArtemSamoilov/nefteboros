# nefteboros — application Dockerfile
#
# PLACEHOLDER. Реальная реализация — в PR `feature/docker-compose`.
#
# Стратегия (multi-stage):
#   Stage 1 (builder)
#     - python:3.12-slim
#     - системные пакеты: build-essential, gcc, libffi-dev, libpq-dev, libgomp1
#     - cmdstan для prophet
#     - pip install -r requirements.txt -r requirements-domain.txt в venv
#
#   Stage 2 (runtime)
#     - python:3.12-slim
#     - копируем venv из builder
#     - копируем код проекта
#     - предзагружаем BGE-M3 модель в /app/models (чтобы старт был быстрым)
#     - ENTRYPOINT: bash -c "python launcher.py"
#
# Размер образа должен быть в пределах 4 GB (см. docs/adr/0009-docker-strategy.md TBD).

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libffi-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# TODO: pip install requirements.txt + requirements-domain.txt
# TODO: pre-download BGE-M3
# TODO: cmdstan для Prophet

# ---

FROM python:3.12-slim AS runtime

WORKDIR /app

# TODO: copy venv from builder
# TODO: copy app code (only what's needed in runtime)
# TODO: ENV/ports/healthcheck

EXPOSE 8000

CMD ["echo", "nefteboros: Dockerfile placeholder, see feature/docker-compose PR"]
