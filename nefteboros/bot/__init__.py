"""Telegram-бот на aiogram.

Будет содержать:
  - main.py     — точка входа (polling или webhook)
  - handlers.py — message handlers
  - middlewares.py — auth (ALLOWED_TG_USER_IDS), rate-limit, logging
  - formatting.py  — markdown ответа с цитатами для Telegram

Бот — отдельный сервис в docker-compose, дёргает наш core через HTTP API
(или прямо импортирует, если в одном процессе).

См. docs/adr/0008-telegram-bot.md (TBD).
"""
