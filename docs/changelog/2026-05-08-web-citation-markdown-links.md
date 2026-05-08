# 2026-05-08 — Markdown-ссылки в web-цитатах

**PR:** `feature/web-citation-markdown-links`
**Связано:** [ADR-0022](../adr/0022-web-search-brave.md).

## Симптом

Артём в скриншоте чата: агент выдаёт топ-3 новости с маркировкой
`Источник: [BBC News Русская служба, bbc.com, web]` — без URL,
некликабельно. Brave **отдаёт** URL в `results[i].url`, но LLM не
использует, потому что в `_TOOL_DESCRIPTION` и `prompts/SYSTEM.md`
формат был прописан без URL.

## Решение

Поменян формат web-цитат на **markdown-ссылку**:

```
[<title>](<url>) — <hostname>, web
```

UI рендерит markdown — заголовок становится кликабельной ссылкой. Hostname
рядом для quick-check tier'а (LLM подсветит верифицированный источник). Без
дублирования имени источника, как было в `[BBC News Русская служба, bbc.com, web]`.

## Изменения

- [`prompts/SYSTEM.md`](../../prompts/SYSTEM.md):
  - Раздел «Маркировка источников» — новая инструкция для web с примером
    `[OPEC keeps quotas](https://www.reuters.com/...) — reuters.com, web`.
  - Anti-hallucination правило расширено: «не сочиняю URL'ы и не подставляю
    плейсхолдеры; если url не отдался — указываю только hostname».
- [`skills/neftegaz_analyst/plugin.py`](../../skills/neftegaz_analyst/plugin.py):
  `_WEB_TOOL_DESCRIPTION` — последний блок переписан с markdown-форматом
  и явным fallback'ом «если url отсутствует — только hostname».

## Что НЕ менялось

- `_serialize_web_hit()` — `url` в JSON отдавался **с самого начала**
  (`SearchHit.url` → JSON `url`). Backend не нужно трогать.
- Other tier1/2/3 классификация не менялась.
- RAG-цитаты (`[Source title, p.X]`) и forecast (`[Forecast: model, CI N%]`)
  — не markdown-ссылки, остались как были (RAG — на корпус документов
  без публичного URL; forecast — мета-метка без источника).

## Тесты

Frontend unit-тестов нет; verify — manual smoke на сервере после deploy.
Smoke-сценарий: «Что заявил Новак на этой неделе?» → ожидаем в выдаче
кликабельные ссылки на reuters/bloomberg/government.ru/etc.

## Deployment

`git pull && systemctl restart nefteboros`. Manifest skill'а **тронут**
(`_WEB_TOOL_DESCRIPTION` изменён) — `content_hash` сдвинется, **может
понадобиться `POST /api/skills/neftegaz_analyst/review`** для unblock'а
extension в runtime mode advanced. Если review fail'ит — починить как
в [PR #28](https://github.com/ArtemSamoilov/nefteboros/pull/28).
