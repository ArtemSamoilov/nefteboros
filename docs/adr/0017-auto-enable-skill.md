# ADR-0017 — Env-driven auto-enable extension skill в initial tool schemas

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/auto-enable-skill` — финальный шаг к функциональному demo.
- **Связано:** ADR-0001 (форк Ouroboros), ADR-0016 (skill wrapper над graph), ADR-0014/0015 (graph internals).

## Контекст и проблема

После PR'ов #8/#9/#10/#12 у нас:
- analyst graph с 0.98 type accuracy и hybrid disambiguation;
- skill `neftegaz_analyst` экспортирует один tool `analyst_query` через PluginAPI v1 с description'ом, содержащим явные триггеры («прогнозы цен Brent / WTI / Urals…», «бюджетная аналитика РФ», «НЕ ИСПОЛЬЗУЙ на: погоду / биткоин»).

**Но**: на первом ответе агент **не видит** `analyst_query` в active tools. Цепочка передачи skill'а агенту в Ouroboros:

1. Skill регистрирует tool через PluginAPI → `extension_loader._tools` registry.
2. `tools/registry.py:396 schemas()` собирает встроенные tools + live extension tools.
3. `tool_policy.py:30 initial_tool_schemas()` **фильтрует** через `is_initial_task_tool(name)`:
   ```python
   return name in CORE_TOOL_NAMES or name in META_TOOL_NAMES
   ```
4. Имя нашего skill'а — `ext_18_r_neftegaz_analyst_analyst_query` — **не в CORE_TOOL_NAMES, не в META_TOOL_NAMES**. → **Не попадает** в initial schemas.
5. `loop.py:451-459` инжектит system message: «You have N core tools loaded. There are M additional tools available — use list_available_tools / enable_tools». Hint без описания.
6. Чтобы получить skill, агент должен (a) `list_available_tools()` → увидеть имя+description в списке non-core; (b) `enable_tools(name)` → добавить в active; (c) `analyst_query(query)`. **3 round-trip'а** до полезного действия.

На простом запросе «прогноз нефти на квартал» LLM с большой вероятностью **не догадается** искать tool — ответит по памяти или fallback. Это полностью обесценивает 4 предыдущих PR'а, где мы строили analyst pipeline.

## Решение

Расширить `is_initial_task_tool()` env-driven white-list'ом extension-skill tools. Skills, явно перечисленные в env `OUROBOROS_AUTO_ENABLE_SKILLS`, попадают в initial schemas вместе с core/meta — на первом ответе агента LLM видит их description через JSON tool spec и выбирает правильно на доменных запросах.

Формат env: `skill_name:short_tool_name[,…]`. Резолв через `extension_loader.extension_surface_name(skill, short)` → full extension tool name.

```bash
OUROBOROS_AUTO_ENABLE_SKILLS=neftegaz_analyst:analyst_query
```

После этого на сервере (и в локальном dev-env через `.env`):

- На первом ответе агент видит `ext_18_r_neftegaz_analyst_analyst_query` в active tools.
- LLM получает `_TOOL_DESCRIPTION` (~600 chars с триггерами «прогнозы цен / Минфин / нефтегаздоходы», anti-триггерами «погода / биткоин») в tool spec.
- На доменный запрос — выбор `analyst_query` сразу, без discovery-round'ов.

## Что в этом PR

```
ouroboros/tool_policy.py                # ИЗМЕНЁН — `_parse_auto_enable_env()`,
                                          расширение `is_initial_task_tool()`,
                                          docstring модуля
.env.example                            # ИЗМЕНЁН — секция с
                                          OUROBOROS_AUTO_ENABLE_SKILLS

tests/test_tool_policy_auto_enable.py   # NEW — 7 тестов:
                                          empty env / whitelisted / multiple /
                                          invalid entries / direct calls /
                                          list_non_core_tools coherence

docs/adr/0017-auto-enable-skill.md      # этот документ
docs/changelog/2026-05-07-auto-enable-skill.md
```

## Аргументация — главные неочевидные решения

### Почему env, а не manifest-флаг

Альтернатива — добавить в `SkillManifest` поле `auto_enable: true` и читать из manifest'ов в `tool_policy`. Чище в смысле «решение про skill — в самом skill'е». Минусы:
- Surface правки шире: `skill_manifest.py` (новое поле) + `skill_loader.py` (передача в LoadedSkill) + `tool_policy.py` (потребление). Три файла Ouroboros core.
- Пол-мир skills'ов под нашим контролем — нет смысла гонять решение через дисциплину manifest-формата.
- Phase 4 review pipeline должен ревьюить новое поле — ещё surface.

Env-вариант — однофайловая правка, нулевой риск поломать Ouroboros. Для проекта с одним domain skill'ом hardcoded skill_name в env — приемлемая цена.

### Почему `skill_name:short_tool_name`, а не просто `skill_name`

Один skill может регистрировать несколько tools. Например, `weather` skill регистрирует `fetch` tool → `ext_9_r_weather_fetch`. Если бы env брал только skill_name, мы не отделили бы какой именно tool auto-enable.

Формат `skill:short` явный, парсится за один split, легко расширяем.

### Почему fail-soft на ошибках парсинга

`_parse_auto_enable_env` ловит любые exceptions (`ImportError` на `extension_loader`, `Exception` на `extension_surface_name`) и пропускает невалидные записи. Причины:
- Loop.py не должен крашиться при битом env — это ухудшит UX для пользователя, который просто опечатался в `.env`.
- При rename / disable одного skill'а из white-list'а — остальные продолжают работать.
- На каждом round'е tool_policy вызывается → быстрый и надёжный fail-soft важнее красивого error message.

### Почему `extension_surface_name` резолв в runtime, а не cache

`_parse_auto_enable_env` парсит env на каждый вызов `is_initial_task_tool`. Альтернатива — `@lru_cache(1)` или module-level `_AUTO_ENABLED = _parse_auto_enable_env()` при import.

Минусы кеша:
- Тесты с `monkeypatch.setenv` не сработают — кеш будет stale.
- Env может меняться runtime (например, при `os.environ.update` в loop runner'е).

В hot-path не критично: на init задачи `is_initial_task_tool` вызывается ~30-50 раз (по числу tools). Парсинг env — `split(",")` + lookup → microseconds. Если станет hot-path — оптимизация в отдельном PR.

### Почему не поднять `analyst_query` в `CORE_TOOL_NAMES`

`CORE_TOOL_NAMES` — встроенные Ouroboros tools (`repo_read`, `code_search`, …). Mixing extension в core ломает abstraction:
- Phase 4 review pipeline предполагает что core tools безусловно доверены, extension-tools требуют review. Поднятие в core нарушает этот invariant.
- Ouroboros upstream (если будем pulling fixes) определяет CORE_TOOL_NAMES — наш патч поверх будет конфликтовать.
- Env-driven white-list — externalize'd config, не fork patch на core.

### Почему правка `list_non_core_tools` коротлация

`list_non_core_tools` использовался в `_setup_dynamic_tools` (loop.py:449-459) для system message «N additional tools available». Если auto-enabled tool попадает и в initial schemas, и в non_core list — агент получит дублирующий hint «enable_tools(...)» для уже active tool'а. Тест `test_list_non_core_excludes_whitelisted` фиксирует corehency: тула, прошедшая is_initial_task_tool, не появляется в non_core.

## Последствия

**Плюсы:**
- **Закрытый блокер production-deploy**: на первом ответе агент сразу видит `analyst_query` description в tool spec → правильный выбор на доменных запросах. Все 4 предыдущих PR'а становятся функциональными.
- 1 round-trip до tool-call'а вместо 3.
- Env-driven — добавление новых auto-enable skills в будущем без правки кода.
- Backward compat: пустой env / отсутствие env → поведение Ouroboros не меняется.
- Минимально инвазивная правка (~50 LOC в одном файле + tests).

**Минусы / риски:**
- Skill name `neftegaz_analyst` hardcoded в env — Артём подтвердил, что переименовывать не будем. При renaming в будущем — env обновляется отдельно.
- На разных провайдерах LLM (kimi-k2 / GigaChat-Max / etc.) точность tool-selection может варьироваться. Real-LLM smoke на сервере покажет.
- `_TOOL_DESCRIPTION` в plugin.py (~600 chars) занимает место в каждом tool spec'е — токенов на context добавляет, но это price of correctness.

**Митигации:**
- Тестовый набор в `tests/test_tool_policy_auto_enable.py` ловит регрессии (env unset, env set, multiple skills, invalid entries, list_non_core coherence).
- Real-LLM smoke на golden questions из `docs/experiments/intent_classifier.md` после deploy — если accuracy выбора `analyst_query` < 90% на доменных запросах, тюнинг description'а в отдельном PR.
- Описание в env (`.env.example` комментарий) объясняет назначение — оператору на сервере понятно зачем.

## Что НЕ в этом PR (явно)

- **Manifest-флаг `auto_enable: true` для SkillManifest** — отвергнут (см. §Аргументация). Можно вернуться, если потребуется покрытие multi-skill scenarios без dependency на env.
- **Кеш парсинга env** — отвергнуто как преждевременная оптимизация. Если станет hot-path в production — отдельный PR.
- **Поднятие в CORE_TOOL_NAMES** — отвергнуто как нарушение abstraction.
- **A/B-тест formulations `_TOOL_DESCRIPTION`** — отдельный PR `feature/skill-description-tuning` после real telemetry.
- **Health check / observability latency tool-selection'а** — будущая инфра.
- **Override identity-секции SYSTEM.md** — закрытый PR #13. Если понадобится — отдельный PR.

## Альтернативы рассмотренные

- **Manifest-флаг `auto_enable: true`** — отвергнуто: surface шире, пол-мир skills'ов под нашим контролем не оправдывает дисциплину manifest-расширений.
- **Hardcoded skill name в `tool_capabilities.py`** — отвергнуто: правка core, конфликт с upstream.
- **Поднятие `analyst_query` в `CORE_TOOL_NAMES`** — отвергнуто: ломает Phase 4 review-trust abstraction.
- **Preinject system message с описанием skill** в `loop.py:451-459` — отвергнуто: дублирует tool description, агент получает один и тот же текст дважды (в hint + в tool spec).
- **`enable_tools` auto-call в loop init** — отвергнуто: меняет invariant что enabled-extra пуст на старте; больше surface.

## Ссылки

- ADR-0001: [docs/adr/0001-fork-ouroboros.md](0001-fork-ouroboros.md) — форк Ouroboros и domain адаптация.
- ADR-0016: [docs/adr/0016-forecast-skill.md](0016-forecast-skill.md) — skill wrapper над analyst graph.
- `ouroboros/tool_policy.py` — точка решения.
- `ouroboros/tool_capabilities.py::CORE_TOOL_NAMES`, `META_TOOL_NAMES`.
- `ouroboros/extension_loader.extension_surface_name(skill, short)` — резолвер имени.
- `ouroboros/loop.py:451-459` — system message hint про non-core tools (после нашего PR'а — менее критичен).
- Эксперимент: [docs/experiments/intent_classifier.md](../experiments/intent_classifier.md) — 0.98 type accuracy на 100-датасете (значимо только если LLM выбирает analyst_query).
