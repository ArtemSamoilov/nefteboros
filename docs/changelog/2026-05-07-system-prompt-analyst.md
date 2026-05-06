# Changelog: feature/system-prompt-analyst — Primary Mission блок в SYSTEM.md

- **Дата:** 2026-05-07
- **PR:** `feature/system-prompt-analyst`
- **ADRs:** [ADR-0017 — Primary Mission в `prompts/SYSTEM.md`](../adr/0017-system-prompt-analyst.md)

## Задача

Закрыть последний known limitation v1.0.0: «identity ещё «I am a self-modifying AI agent»» (см. project memory). После PR #12 (`feature/forecast-skill`) skill `neftegaz_analyst` зарегистрирован, но default Ouroboros systemprompt не направляет агента к нему. Без этого PR'а на demo агент может не вызвать `analyst_query` на нефтегазовый запрос.

## Контекст

- `prompts/SYSTEM.md` (883 строки) — наследие upstream Ouroboros, объявляет идентичность «becoming personality / self-modifying AI».
- Loadёт `ouroboros/context.py:683` как первый блок static_text перед каждым LLM-call'ом.
- Содержит критическую machinery (tools/safety/memory/decision-gate), которую нельзя ломать.
- **Решение** — prepend новой секции `# Primary Mission` в начало файла. Self-modify mechanics остаются ниже без изменений.

## Что сделано

### `prompts/SYSTEM.md` — prepend Primary Mission блока (~95 строк)

Новая секция в начале файла:

1. **Заголовок**: `# Primary Mission: Старший аналитик нефтегазового рынка (Sber CIB)` — доменная роль для Sber CIB.

2. **Главное правило**: «для вопросов про нефть/газ/Минфин/бюджет/нефтегаздоходы/OPEC+ — обязательно вызови `ext_<len>_<token>_analyst_query` ПЕРВЫМ, не отвечая по памяти». Объяснение, что tool сам делает (rule-based + GigaChat-2-Max LLM, forecast(), synthesis с цитатами).

3. **Когда вызывать `analyst_query`**: explicit-список triggers с конкретными формулировками (Brent/WTI/Urals/TTF/Henry Hub/urals_minfin_blend, Минфин/бюджет/нефтегаздоходы, нерегулярные марки нефти Bonny Light/Sokol/Maya/Tapis/Forcados, JKM Asian LNG, OPEC+).

4. **Когда НЕ вызывать**: общение/приветствие/благодарность, system questions про себя, погода/биткоин/курс рубля → honest refuse без tool, self-modify/git/repo → обычные Ouroboros tools.

5. **Формат ответа после tool-call'а**:
   - Lead with `synthesis` (готовый текст из LLM-узла графа).
   - Сохранять `citations` дословно (machine-validated).
   - Упоминать `validation_warnings` если non-empty.
   - Честно говорить про `forecast_errors`.
   - Не пересказывать по памяти — tool это single source of truth для нефтегазовых чисел.

6. **Известные ограничения analyst pipeline**:
   - Tool возвращает base-case без RAG/web overlay до merge `feature/rag-integration` / `feature/web-integration`.
   - Stat-модель forecast'а систематически промахивается на shock-events (Iran-shock 2026-Q1: −38% MISS CI80).
   - 2 known edge cases (JKM Asian LNG → out_of_scope, Юралс → forecast_simple) — переформулировать запрос если creator недоволен.

Self-modify-машинерия Ouroboros (BIBLE.md, identity.md, scratchpad, knowledge tools, Tools section, Safety Agent, Files and Paths, Versioning, Drift Detector) **сохраняется без изменений** — она работает как внутренняя инфраструктура для git-операций / refactor'ов self.

### Tests: `tests/test_system_prompt_smoke.py` — 10 проверок

- Файл существует и читается.
- Primary Mission блок — первый H1.
- «I Am Ouroboros» секция сохранена и идёт ПОСЛЕ Primary Mission.
- `analyst_query` упомянут.
- Ключевые активы (Brent, WTI, Urals, TTF, Henry Hub) явно перечислены.
- РФ-keyword'ы (Минфин, бюджет, нефтегаздоход) явно объявлены.
- «Когда НЕ вызывать» секция присутствует.
- Формат ответа (synthesis, citations) объяснён.
- Известные ограничения (RAG/web, shock) присутствуют.
- Размер файла в разумных границах (<60K chars, <1100 строк).

**Итого: 87/87 passed** (53 intent_classifier + 8 graph_smoke + 7 llm_disambiguate + 9 neftegaz_skill_smoke + 10 system_prompt_smoke).

### ADR

- `docs/adr/0017-system-prompt-analyst.md` — обоснование prepend-стратегии (а не replace), почему identity Ouroboros остаётся, почему секция в начале а не в `## Tools`, почему explicit triggers, почему явные limitations в промпт.

## Что НЕ в этом PR (явно)

- **Полная замена identity** Ouroboros'а на аналитика — `feature/full-identity-rebrand`, отдельный PR. Объём огромный (BIBLE.md, ARCHITECTURE.md, knowledge topics).
- **Удаление Consciousness/Evolve UI кнопок** в `web/` — `feature/ui-rebrand`. UI всё ещё показывает Ouroboros lore.
- **A/B-тест разных формулировок** analyst block'а — оптимизация tone'а / triggers через golden-eval с creator-questions. Отдельный PR `feature/system-prompt-tuning` после real telemetry.
- **Override identity-секции для «расскажи о себе»** — если в demo возникнут такие запросы, отдельный PR.
- **Перевод полного системного промпта на русский** — сейчас mixed (English mechanics + Russian analyst block); тестировать после deploy.

## Тесты

- AST OK на новых .py.
- 87/87 tests passed без сетевых вызовов.
- Real-LLM smoke на сервере после deploy — отдельный manual workflow.

## Файлы

**Добавлено (3 файла):**
- `docs/adr/0017-system-prompt-analyst.md`
- `docs/changelog/2026-05-07-system-prompt-analyst.md` (этот файл)
- `tests/test_system_prompt_smoke.py`

**Изменено (1 файл):**
- `prompts/SYSTEM.md` — prepend Primary Mission блока (~95 строк, +5K chars).

**Удалено:** —

## Связанные документы

- ADR-0017: [docs/adr/0017-system-prompt-analyst.md](../adr/0017-system-prompt-analyst.md)
- ADR-0001: [docs/adr/0001-fork-ouroboros.md](../adr/0001-fork-ouroboros.md) — форк Ouroboros и доменная адаптация
- ADR-0014/0015/0016: graph + LLM disambiguate + skill wrapper
- Эксперимент: [docs/experiments/intent_classifier.md](../experiments/intent_classifier.md) — 0.98 type accuracy
- Архитектура: [docs/architecture.md](../architecture.md)
- Loader: `ouroboros/context.py:683` (`safe_read("prompts/SYSTEM.md")`)
- Предыдущие PR: #8, #9, #10, #12
