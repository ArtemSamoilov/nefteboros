# Деплой на Timeweb Cloud

> PLACEHOLDER. Реальная инструкция — в PR `feature/docker-compose`.

## Конфигурация сервера

Рекомендуемая VDS:
- **2 vCPU**
- **4 GB RAM**
- **80 GB SSD**
- ОС: Ubuntu 22.04 / 24.04 LTS

Обоснование: образ ~3-4 GB, BGE-M3 (~2 GB) в RAM при инференсе через API не нужен (используем внешний или загружаем по запросу). Запас по диску — на расширение PDF-корпуса.

## Шаги установки

```bash
# TODO: подробная инструкция в feature/docker-compose
# 1. Установить docker + docker compose plugin
# 2. git clone https://github.com/ArtemSamoilov/nefteboros.git
# 3. cp .env.example .env && заполнить
# 4. (опц.) положить PDF в data/corpus/
# 5. python scripts/index_corpus.py     (или внутри контейнера)
# 6. docker compose -f deploy/docker-compose.yml up -d
```

## Безопасность

- TG-бот — `TELEGRAM_ALLOWED_USER_IDS` обязательно заполнен
- Web UI — за nginx с basic auth (или Cloudflare Access)
- `.env` — права 0600, owner root
- Логи — в `/var/log/nefteboros/` с logrotate
