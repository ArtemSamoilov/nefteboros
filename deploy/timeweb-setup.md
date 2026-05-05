# Деплой на Timeweb Cloud

> Инструкция отражает фактический deploy на Timeweb VDS 186.246.2.190 (2026-05-04). Docker'овский путь — TBD в `feature/docker-compose`. Текущая инструкция — venv + systemd.

## Конфигурация сервера

Рекомендуемая VDS:
- **2 vCPU**
- **4 GB RAM**
- **80 GB SSD** (можно 50, но 80 даёт запас под PDF и vector store)
- ОС: Ubuntu 22.04 / 24.04 LTS

При создании VDS добавь свой публичный SSH-ключ через UI Timeweb. Пароль root оставь сложным — мы будем работать только по ключу.

## Шаги развёртывания (venv + systemd)

### 1. Системные пакеты

```bash
apt update
apt install -y python3-venv python3-pip python3-dev build-essential libffi-dev libssl-dev pkg-config git
```

Python 3.10+ обычно уже стоит в Ubuntu 24.04 (3.12.x).

### 2. Клонирование

```bash
cd /root
git clone https://github.com/ArtemSamoilov/nefteboros.git
cd nefteboros
```

### 3. venv и зависимости

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel setuptools
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install langchain-core langchain langchain-openai langchain-gigachat python-dotenv
```

> Полные `requirements-domain.txt` (chromadb, sentence-transformers, prophet, aiogram, streamlit) ставим, когда будут готовы соответствующие компоненты — это тяжёлые зависимости.

### 4. Настройка `.env`

Скопируй `.env.example` и заполни ключи:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

**Минимально для baseline (web UI + чат):**
- `OPENAI_COMPATIBLE_BASE_URL=https://hydragpt.ru/v1`
- `OPENAI_COMPATIBLE_API_KEY=hydra_<ваш_токен>`
- `OUROBOROS_MODEL=openai-compatible::kimi-k2p6` (или другая модель из `/models`)
- `OUROBOROS_MODEL_LIGHT=openai-compatible::glm-5`
- `OUROBOROS_MODEL_CODE=openai-compatible::kimi-k2p6`
- `OUROBOROS_DATA_DIR=/root/nefteboros/data/ouroboros`
- `TOTAL_BUDGET=10.0`

**ВАЖНО про префикс `openai-compatible::`** — без него Ouroboros не знает куда роутить и упадёт fallback'ом на anthropic, который без `ANTHROPIC_API_KEY` вернёт «Missing credentials».

**Подтверждённый список моделей HydraGPT** (`GET /v1/models`):
`kimi-k2p6`, `kimi-k2p5`, `glm-5p1`, `glm-5`, `deepseek-v4-pro`, `deepseek-v3p2`, `deepseek-v3p1`, `minimax-m2p7`, `gpt-oss-120b`.

### 5. systemd unit

Скопируй готовый unit:

```bash
cp deploy/nefteboros.service /etc/systemd/system/nefteboros.service
mkdir -p /var/log/nefteboros
systemctl daemon-reload
systemctl enable nefteboros
systemctl start nefteboros
```

Проверка:

```bash
systemctl status nefteboros --no-pager | head -15
ss -tlnp | grep ':8765'
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8765/
```

Должны быть: `active (running)`, listen на `127.0.0.1:8765`, `HTTP 200`.

### 6. Доступ к UI

По умолчанию server слушает только loopback (`OUROBOROS_SERVER_HOST=127.0.0.1`). Открывать наружу без auth — небезопасно. Делай SSH tunnel с локальной машины:

```bash
ssh -i ~/.ssh/id_ed25519_nefteboros -L 8765:localhost:8765 root@<server_ip>
```

Открой `http://localhost:8765` в браузере.

Альтернатива (когда будем делать публичный доступ): `OUROBOROS_SERVER_HOST=0.0.0.0` + nginx с basic auth + Let's Encrypt SSL — отдельный PR.

## Логи и управление

```bash
# логи (все)
tail -f /var/log/nefteboros/server.log

# логи (только ошибки)
journalctl -u nefteboros -p err -n 50

# рестарт
systemctl restart nefteboros

# остановить
systemctl stop nefteboros
```

## Известные проблемы

### Telegram API заблокирован

`api.telegram.org` (149.154.166.110) на network-уровне не доступен с большинства IP-адресов Timeweb (РКН-блок по IP-диапазонам). Polling Bot API не работает.

Workaround:
- VPN до non-РФ узла (WireGuard / OpenVPN)
- HTTP_PROXY env с прокси-сервером вне РФ
- Webhook + reverse-proxy с SSL (Telegram пушит к нам, но он сам должен достичь нашего IP — RKN режет и в эту сторону)
- Использовать MTProto-клиент (telethon/pyrogram) вместо Bot API HTTP

Запланировано в отдельный PR `feature/telegram-proxy`.

### consciousness/evolution кнопки в UI

Кнопки `Consciousness`, `Evolve`, `Review` в верхней панели UI остались от upstream Ouroboros. Backend подсистем удалён в PR #2 (`feature/rip-self-modify`). Клик по этим кнопкам приведёт к ошибке. Не использовать. Удаление из UI — `feature/ui-cleanup` PR.

## Безопасность

- `.env` — права 0600, owner root
- TG-бот — `TELEGRAM_ALLOWED_USER_IDS` обязательно заполнен (когда заработает)
- Web UI — за nginx с basic auth (или Cloudflare Access) при публичном доступе
- SSH — только по ключу, password auth выключен (`PasswordAuthentication no` в `/etc/ssh/sshd_config`)
- Логи — в `/var/log/nefteboros/` с logrotate (TBD)

## Чек-лист после деплоя

- [ ] `systemctl status nefteboros` → active
- [ ] `curl http://127.0.0.1:8765/` → HTTP 200
- [ ] SSH tunnel → UI открывается в браузере
- [ ] В чате — ответ от модели (без `Missing credentials` ошибок)
- [ ] `Costs` в UI растёт после первого запроса
- [ ] `journalctl -u nefteboros -p err` — нет красных ошибок (warning'и допустимы)
