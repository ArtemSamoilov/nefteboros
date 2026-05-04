---
name: neftegaz_analyst
description: Старший аналитик нефтегазового рынка — RAG по отраслевым отчётам, веб-поиск, прогноз цен Brent.
version: 0.0.1
type: extension
entry: plugin.py
permissions: [net, tool, route, widget]
env_from_settings: []
when_to_use: User asks about oil/gas industry — markets, OPEC+, sanctions, supply/demand, Brent/WTI/Urals pricing, forecasts, current quotes, regulator statements.
---

# Skill: neftegaz_analyst

Skill реализует AI-агента в роли «Старший аналитик нефтегазового рынка» (см. [ТЗ](../../docs/tz/original.md)).

> **Статус:** placeholder. Реальная реализация — в PR `feature/skill-integration`.

## Зарегистрированные tools

| Tool | Назначение |
|---|---|
| `analyst_query` | Основной endpoint — отвечает на нефтегазовые вопросы. Внутри LangGraph subgraph: classify_intent → {rag\|web\|forecast} → synthesize → validate_citations. |
| `brent_forecast` | Прогноз цены Brent на N месяцев (ARIMA + Prophet, CI 80/95%). |

## Зарегистрированные routes (HTTP)

| Endpoint | Назначение |
|---|---|
| `GET /api/extensions/neftegaz_analyst/health` | Проверка готовности RAG-индекса, доступности LLM. |
| `POST /api/extensions/neftegaz_analyst/query` | Прокси для analyst_query (для интеграций). |

## Зарегистрированные UI tabs

| Tab | Назначение |
|---|---|
| `analyst` | Чат с аналитиком — отдельная вкладка в Ouroboros web UI. |

## Permissions

- `net` — для веб-поиска через Brave API и LLM-вызовов
- `tool` — register_tool
- `route` — register_route
- `widget` — register_ui_tab

## Архитектурные решения

См. [ADR-0001](../../docs/adr/0001-fork-ouroboros.md).
