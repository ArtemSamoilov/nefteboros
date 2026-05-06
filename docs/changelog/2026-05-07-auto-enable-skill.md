# Changelog: feature/auto-enable-skill — env-driven auto-enable skill в initial schemas

- **Дата:** 2026-05-07
- **PR:** `feature/auto-enable-skill`
- **ADRs:** [ADR-0017 — Env-driven auto-enable extension skill](../adr/0017-auto-enable-skill.md)

## Задача

Закрыть блокер: на первом ответе агент **не видел** `analyst_query` в active tools (skill попадает только после `list_available_tools` + `enable_tools` discovery, 2 round-trip'а). На простом запросе «прогноз нефти на квартал» LLM мог не догадаться искать tool — отвечал по памяти, обесценивая 4 предыдущих PR'а с analyst pipeline.

## Контекст

Ranal цепочки в Ouroboros:

1. Skill регистрирует tool через PluginAPI → `extension_loader._tools` registry.
2. `tools/registry.py::schemas()` собирает встроенные + live extension tools.
3. `tool_policy.py:30 initial_tool_schemas` фильтрует через `is_initial_task_tool(name)`:
   ```python
   return name in CORE_TOOL_NAMES or name in META_TOOL_NAMES
   ```
4. Имя нашего skill'а (`ext_18_r_neftegaz_analyst_analyst_query`) **не в CORE_TOOL_NAMES, не в META_TOOL_NAMES** → не попадает в initial schemas.
5. `loop.py:451-459` инжектит generic hint без описания tool'ов: «You have N core tools loaded. M additional available. Use list_available_tools / enable_tools».
6. Агент должен сделать 2 discovery-round'а до полезного действия.

«Системный промпт агента, где скиллы прописаны» — это **JSON tool schemas** передаваемых через `tools=` параметр в LLM API call'ах. И **именно туда** наш skill сейчас не попадал.

## Что сделано

### `ouroboros/tool_policy.py` — расширение

- `_parse_auto_enable_env()` — читает `OUROBOROS_AUTO_ENABLE_SKILLS` env, парсит формат `skill_name:short_tool_name[,…]`, резолвит через `extension_loader.extension_surface_name()` → set full ext-tool names.
- `is_initial_task_tool(name)` дополнен проверкой на whitelisted set'е.
- Fail-soft: empty env / битые записи / отсутствующий extension_loader → возвращает empty set, не raise.

### `.env.example` — секция документации

```bash
# Auto-enable extension-skill tools в initial schemas агента (см. ADR-0017).
# Формат: skill_name:short_tool_name[,…]
# Без этого extension-tool появляется только после list_available_tools +
# enable_tools (2 round'а discovery). С этим — сразу в active tools на первом
# ответе агента, LLM видит description и выбирает на доменных запросах.
OUROBOROS_AUTO_ENABLE_SKILLS=neftegaz_analyst:analyst_query
```

### `tests/test_tool_policy_auto_enable.py` — 7 тестов

- `test_extension_surface_name_resolves_as_expected` — sanity: `extension_surface_name("neftegaz_analyst", "analyst_query") == "ext_18_r_neftegaz_analyst_analyst_query"`.
- `test_initial_includes_only_core_when_env_unset` — без env extension tool отсутствует в initial.
- `test_initial_includes_whitelisted_extension` — с env=`neftegaz_analyst:analyst_query` наш tool в initial, weather (не whitelisted) — нет.
- `test_multiple_skills_supported` — запятая-отделённый list.
- `test_invalid_entries_are_skipped_silently` — пустые / битые записи игнорируются, валидные работают.
- `test_is_initial_task_tool_direct_calls` — прямая проверка для core / whitelisted / random.
- `test_list_non_core_excludes_whitelisted` — `list_non_core_tools` не возвращает auto-enabled extension tool (coherency с initial schemas, чтобы system-message hint не дублировал).

**Тесты: 7/7 passed**.

### ADR

- `docs/adr/0017-auto-enable-skill.md` — обоснование env-driven подхода (vs manifest-флаг, vs CORE_TOOL_NAMES patch), формат `skill:short`, fail-soft strategy, почему без cache.

## Что НЕ в этом PR (явно)

- **Manifest-флаг `auto_enable: true` для SkillManifest** — отвергнуто как избыточно для проекта с одним domain skill'ом. Возможен возврат если понадобится multi-skill auto-enable scenario.
- **Кеш парсинга env (`@lru_cache`)** — отвергнуто как преждевременная оптимизация. Парсинг ~30-50 calls на init задачи, microseconds.
- **Поднятие `analyst_query` в `CORE_TOOL_NAMES`** — отвергнуто как нарушение abstraction Phase 4 review-trust.
- **A/B-тест `_TOOL_DESCRIPTION` formulations** — отдельный PR `feature/skill-description-tuning` после real telemetry.
- **Health check / observability latency tool-selection'а** — будущая инфра.

## Тесты

- AST OK: `ouroboros/tool_policy.py`, `tests/test_tool_policy_auto_enable.py`.
- pytest: 7/7 passed (`tests/test_tool_policy_auto_enable.py`).
- Existing skill-related tests (intent_classifier 53, graph_smoke 8, llm_disambiguate 7, neftegaz_skill_smoke 9) не затронуты этим PR'ом — изменение в Ouroboros core, не в наших файлах.

## Файлы

**Добавлено (3 файла):**
- `docs/adr/0017-auto-enable-skill.md`
- `docs/changelog/2026-05-07-auto-enable-skill.md` (этот файл)
- `tests/test_tool_policy_auto_enable.py`

**Изменено (2 файла):**
- `ouroboros/tool_policy.py` — `_parse_auto_enable_env`, расширение `is_initial_task_tool`, docstring модуля.
- `.env.example` — секция OUROBOROS_AUTO_ENABLE_SKILLS=neftegaz_analyst:analyst_query.

**Удалено:** —

## Operational note

После deploy на Timeweb:
1. `git pull`.
2. Phase 4 `review_skill neftegaz_analyst` (триметодельный AI review) + `enable_skill neftegaz_analyst`.
3. **В `/root/nefteboros/.env`** добавить строку:
   ```
   OUROBOROS_AUTO_ENABLE_SKILLS=neftegaz_analyst:analyst_query
   ```
4. Restart `nefteboros` service.
5. Real-LLM smoke: golden questions из `docs/experiments/intent_classifier.md` → проверить, что агент **сразу** выбирает `analyst_query` на доменных запросах без `list_available_tools` round'а.

## Связанные документы

- ADR-0017: [docs/adr/0017-auto-enable-skill.md](../adr/0017-auto-enable-skill.md)
- ADR-0016: [docs/adr/0016-forecast-skill.md](../adr/0016-forecast-skill.md) — skill wrapper над graph.
- ADR-0014/0015: [docs/adr/0014-langgraph-subgraph.md](../adr/0014-langgraph-subgraph.md), [docs/adr/0015-llm-disambiguate.md](../adr/0015-llm-disambiguate.md).
- Эксперимент: [docs/experiments/intent_classifier.md](../experiments/intent_classifier.md) — 0.98 type accuracy.
- Loader: `ouroboros/tool_policy.py`, `ouroboros/loop.py:534`, `ouroboros/extension_loader.py::extension_surface_name`.
- Предыдущие PR: #8, #9, #10, #12.
