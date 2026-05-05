# 2026-05-05 — Server fixes (feature/server-fixes)

## Задача

Зафиксировать в код hot-fixes, выявленные при первом deploy на Timeweb VDS 186.246.2.190 (2026-05-04 → 2026-05-05). Привести `deploy/` в production-ready состояние.

## Контекст

После merge PR #3 (LLM-провайдеры) подняли сервер на Timeweb через venv + systemd. Прошли через несколько проблем:

1. **`max_tokens=16384` ломает HydraGPT/kimi-k2p6** — модель требует `stream=true` для значений > 4096. Получали 12 BadRequest подряд при первом тесте чата.
2. **`OUROBOROS_MODEL=kimi-k2p6` без префикса** — Ouroboros не знал куда роутить, падал в fallback-цепочку (kimi → anthropic → openai), все три без креда.
3. **`data/ouroboros/` создаётся на старте сервера** — попадает в `git status` как untracked, спамит warning'и в логах startup-проверок.
4. **`nohup` детачится плохо** — после моего `pkill` не поднимался автоматом. Нужен systemd с `Restart=on-failure`.

После применения всех правок чат подтверждённо ответил по-русски через `kimi-k2p6` (2026-05-05 ~00:11 МСК).

## Что сделано

### Код

**`ouroboros/llm.py`** — `max_tokens: int = 16384` → `4096` в двух точках:
- `LLMClient.chat()` (строка 530)
- `LLMClient.chat_with_target()` (строка 562)

Третья точка (`vision_query` на 1556) уже была 4096 — не трогаем. Аргументация — [ADR-0008](../adr/0008-llm-max-tokens.md).

**`.gitignore`** — добавлено `/data/ouroboros/` (runtime drive Ouroboros'а: state, memory, logs, dialogue_blocks).

### Deploy

**`deploy/nefteboros.service`** (новый) — systemd unit с:
- `Type=simple`, `Restart=on-failure`, `RestartSec=5`
- `EnvironmentFile=/root/nefteboros/.env` — все наши env vars подгружаются автоматом
- `ExecStart=/root/nefteboros/.venv/bin/python /root/nefteboros/server.py`
- `StandardOutput/Error=append:/var/log/nefteboros/server.log`
- `LimitNOFILE=65536`

**`deploy/timeweb-setup.md`** (полностью переписан) — пошаговая инструкция, отражающая фактический deploy:
- Системные пакеты (apt install)
- Клонирование + venv + минимальная установка зависимостей
- Заполнение `.env` с правильным форматом моделей (`openai-compatible::<name>`)
- Установка systemd unit
- SSH tunnel для UI
- Известные проблемы (Telegram blocked by RKN, consciousness/evolve кнопки от upstream)
- Чек-лист после деплоя

### ADR

**`docs/adr/0008-llm-max-tokens.md`** — обоснование снижения дефолта max_tokens.

## Что НЕ в этом PR (отложено)

- **Telegram bridge через VPN/прокси** — `api.telegram.org` блокируется RKN на network-уровне в IP-диапазонах Timeweb. TCP timeout. Нужен VPN-узел вне РФ или MTProto-клиент. Артём отложил до 2026-05-05.
- **Identity replacement** — Ouroboros UI использует системный промпт «I am a self-modifying AI agent». Заменим на «Старший аналитик нефтегазового рынка» в `feature/skill-integration` (PR #10).
- **UI cleanup** — кнопки `Consciousness`, `Evolve`, `Review` остались в UI после выпила backend'а в PR #2. Уберём в `feature/ui-cleanup`.
- **Fallback на anthropic** — Ouroboros по дефолту падает на `anthropic/claude-sonnet-4.6` при провале primary, что без ANTHROPIC_API_KEY бесполезно. Переключить на другую HydraGPT-модель — отдельный PR.
- **Public access** — сейчас сервер слушает только loopback. Для публичного доступа нужен nginx + basic auth + Let's Encrypt SSL — отдельный PR.
- **logrotate** для `/var/log/nefteboros/server.log` — отдельный мини-PR.

## Тесты

- AST-парсинг `ouroboros/llm.py` — OK (правка только в дефолтных значениях)
- Smoke на сервере 2026-05-05 — чат ответил, HTTP 200 на UI, systemd active, 6 worker'ов

## Что подтвердили (production-ready baseline)

- Web UI работает на 127.0.0.1:8765 ✓
- Ouroboros core после выпила self-modify не сломан ✓
- Интеграция с HydraGPT через `OPENAI_COMPATIBLE_BASE_URL` ✓
- Префикс `openai-compatible::kimi-k2p6` корректно роутит ✓
- `max_tokens=4096` совместим с HydraGPT-моделями без streaming ✓
- systemd auto-restart переживает падения ✓

## Файлы

**Изменены (2):**
- `ouroboros/llm.py`
- `.gitignore`

**Добавлены (3):**
- `deploy/nefteboros.service`
- `docs/adr/0008-llm-max-tokens.md`
- `docs/changelog/2026-05-05-server-fixes.md`

**Полностью переписан (1):**
- `deploy/timeweb-setup.md`

## Связанные документы

- ADR-0008: [docs/adr/0008-llm-max-tokens.md](../adr/0008-llm-max-tokens.md)
- timeweb-setup: [deploy/timeweb-setup.md](../../deploy/timeweb-setup.md)
- Предыдущий PR: PR #3 `feature/llm-providers`
