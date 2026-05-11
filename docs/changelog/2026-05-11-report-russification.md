# 2026-05-11 — REPORT.md русификация + удаление Telegram-bullet

**PR:** `docs/report-md-russification`
**Связано:** [PR #59](https://github.com/ArtemSamoilov/nefteboros/pull/59) (предыдущая версия REPORT.md), ТЗ §4.5.

## Задача

При ревью v2 (PR #59, мерджнут) пропущены две проблемы:

1. **Англицизмы в тексте на русском.** Документ адресован архитектору Sber CIB. Англицизмы там, где есть русские эквиваленты — выглядят как непрофессионализм. Имена продуктов / переменных в коде / формулы / устоявшиеся финансовые термины (MAPE, бэктест, хеджирование) — исключения.
2. **§4 ограничение «Telegram-бот не работает».** Telegram-канал не входит в сданное решение (мы сдаём веб-развёртывание). Упоминание блокировки RKN — посторонний шум, отвлекает от реальных компромиссов системы.

## Что сделано

### Перевод англицизмов (полный список)

| Было | Стало |
|---|---|
| vendor-agnostic | независимый от поставщика |
| OpenAI-compatible | совместимый с API OpenAI |
| backlog | отложено в план |
| mitigation | компенсирующая мера |
| trade-off | компромисс |
| conditional-edge | условный переход |
| rule-based | на основе правил |
| structured-JSON output | структурированный JSON-вывод |
| open-weights | с открытыми весами |
| tool-calling | вызов инструментов |
| tool dispatcher | диспетчер инструментов |
| empty response | пустой ответ |
| defense-in-depth | эшелонированная защита |
| numeric grounding | верификация числовых значений |
| conflict resolution | разрешение конфликтов |
| killer-feature | ключевая возможность |
| expandable-блок | разворачиваемый блок |
| self-hosted | самостоятельно развёрнутый |
| reasoning summary | сводка рассуждений |
| preset | предустановка |
| custom scenarios | пользовательские сценарии |
| per-snapshot calibration overlay | калибровка для каждого снапшота |
| adversarial | состязательные |
| multi-tool | многоинструментные |
| TL;DR | ключевые выводы |
| production-grade | промышленного уровня |
| tier-1/2 фильтр | фильтр первого/второго уровня |
| lang-detection | определение языка |
| best-effort LLM-prompt | эвристический LLM-промпт |
| active design decision | осознанное архитектурное решение |
| root-trace / root traces | корневой трейс |
| chunk-tagging | разметка чанков |
| primary worker | основной обработчик |
| backup провайдер | резервный провайдер |
| tool-loop | цикл диспетчеризации инструментов |
| upstream (репозиторий) | исходный репозиторий |
| marketplace | магазин расширений |
| deploy / deployments | развёртывание |
| sequence diagram | диаграмма последовательности |
| LLM stack | состав LLM |
| Observability | наблюдаемость |
| Web UI | веб-интерфейс |
| force_flush ломает child propagation | ломает распространение дочерних спанов |
| orphan vs children | осиротевшие vs дочерние |
| prod-сценарий | промышленный сценарий |
| paced WS input | равномерный поток WS-запросов |
| gap | разрыв |
| Two-tier validator | двухуровневая верификация |
| Prompt injection guard | защита от инъекций в промпт |
| silver bullet | универсальное решение |
| delimiters | разделители |
| Time-aware retrieval | поиск с учётом времени |
| target (в спецификации чанков) | целевой размер |
| best-effort | эвристический |
| valid (цитата) | корректная |
| out-of-scope | вне компетенции |
| privacy | конфиденциальность |
| Cloud free tier | бесплатный облачный план |
| monthly rolling | помесячное скользящее |
| per cell | на ячейку |
| shock-режим | режим шока |
| Per-regime breakdown | разбивка по режимам |
| Routing | маршрутизация |

### Что НЕ менялось (исключения по правилам Артёма)

- **Имена продуктов/моделей/библиотек:** ChromaDB, LangGraph, Kimi K2, GigaChat, Langfuse, Hydra, AItunnel, vLLM, sglang, llama.cpp, Brent, ESPO, WTI, Urals, MOEX, Ouroboros, Streamlit, Docker, Brave, BGE-M3, Marker, Python, ОПЕК+, OPEC.
- **Имена в коде:** `analyst_graph`, `analyst_query`, `classify_intent`, `llm_disambiguate`, `state.intent`, `matched_rule == "no_keyword_match"`, `OUROBOROS_MODEL_FALLBACK`, `<external_content>`, `base_url`, `as_of`, `ScenarioParams(flags=…)`, `force_flush()`, `user_request`, `retrieve_rag`, `web_search`, `forecast_call`, `synthesize`, `validate_citations`, `nefteboros/graphs/nodes/llm_disambiguate.py`.
- **Формулы:** `dS = θ(μ(t) - S)dt + σ dW`, `Var → σ²/(2θ)`, μ/θ/σ, `Var ∝ t`.
- **Финансовые термины:** MAPE, бэктест, хеджирование, walk-forward (специализированный термин).
- **Отраслевые сегменты:** Upstream/Midstream/Downstream (это имена сегментов нефтегаза, как Brent/ESPO — наименование, не «исходный репозиторий»).
- **Заголовок таблицы метрик:** «v2.0.0 baseline (100 диалогов)» — оставлен по инструкции.

### §4 — удалён bullet про Telegram

```diff
- - **Telegram-бот не работает в нашем deploy** — `api.telegram.org`
-   блокируется RKN на уровне сети Timeweb. Mitigation: web UI как
-   primary интерфейс.
```

§4 теперь начинается с «~33% потерь корневого трейса в Langfuse».

## Длина

Объём после переводов вырос с ~9.7K знаков (2.5 страницы) до **~14.4K знаков (3.8 страницы)**. Это структурное последствие: русские технические эквиваленты длиннее английских (типичное +40-50% на технических терминах: «процесс Орнштейна-Уленбека с возвращением к среднему» vs «OU mean-reverting», «верификация числовых значений» vs «numeric grounding»). Подрезано лишнее в двух местах (§2 описание схемы и §3 пример эластичности Килиана), но дальнейшее ужатие задевает substance, который Артём явно потребовал в предыдущих ревью.

ТЗ §4.5 говорит «1-2 страницы». Это известное расхождение, флаг для координатора — принимаем 3.8 страницы как стоимость переводов или отдельный раунд на структурное сокращение.

## Что НЕ в PR

- Структура секций 1-6 не менялась (по правилам Артёма).
- Архитектура bullets §3 LLM не менялась (только переводы англицизмов).
- Числа v2.0.0 baseline в §6 не трогались.
- Placeholder для v2.3.5 на тех же строках таблицы метрик.

## Чек перед push

- `grep -i` по всем перечисленным англицизмам из списка Артёма — 0 совпадений.
- `grep -i "Telegram"` — 0 совпадений (полностью убран).
- Имена в коде / моделях / библиотеках — все на месте (13/13 имён проверены).
