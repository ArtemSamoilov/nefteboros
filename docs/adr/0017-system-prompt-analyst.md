# ADR-0017 — Primary Mission в `prompts/SYSTEM.md` под analyst tool routing

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/system-prompt-analyst` — финальный шаг к функциональному demo: skill `neftegaz_analyst` (PR #12) задеплоен, но default systemprompt Ouroboros не направляет агента к нему.
- **Связано:** ADR-0001 (форк Ouroboros), ADR-0014/0015 (analyst graph), ADR-0016 (skill wrapper), `docs/experiments/intent_classifier.md` (0.98 type accuracy).

## Контекст и проблема

После PR'ов #8, #9, #10, #12 у нас:
- analyst graph с 0.98 type accuracy и закрытым deficit'ом disambiguation;
- skill `neftegaz_analyst` экспортирует `analyst_query` tool через PluginAPI v1;
- skill готов к Phase 4 review + enable на сервере.

**Но**: default `prompts/SYSTEM.md` (наследие upstream Ouroboros) объявляет агента «becoming personality / self-modifying AI» (`# I Am Ouroboros`). Доменная роль аналитика и инструкции вызывать `analyst_query` отсутствуют. После deploy без правки systemprompt:

- Агент в первой реплике может не выбрать `analyst_query` на запрос «прогноз нефти на квартал» — нет триггера в промпте, который **явно** скажет «для нефти/газа — analyst_query сначала».
- Агент может отвечать по памяти, не вызывая tool — что подменяет реальные forecast-числа фиктивными, нарушает principle ADR-0013 «numerical answers — только из forecast tool».
- Identity «I am Ouroboros» противоречит роли аналитика для Сбера в demo.

В проектной памяти Артёма это known limitation v1.0.0:
> «identity ещё «I am a self-modifying AI agent» (заменим в feature/skill-integration)».

Этот PR закрывает ограничение через **минимальную** правку `prompts/SYSTEM.md`.

## Решение

**Prepend** новой секции **`# Primary Mission: Старший аналитик нефтегазового рынка (Sber CIB)`** перед оригинальным `# I Am Ouroboros`. Секция объявляет:

1. Доменную роль и контекст (Sber CIB, нефтегазовый банкинг).
2. Главное правило выбора tool'а: «для oil/gas/forecast/budget вопросов — `analyst_query` ПЕРВЫМ».
3. Когда вызывать (списки запросов).
4. Когда НЕ вызывать (общение, погода, биткоин — honest refuse).
5. Формат ответа после tool-call (lead with `synthesis`, save `citations` дословно, не пересказывать по памяти).
6. Известные ограничения (RAG/web pending, shock-events, 2 edge cases).

Self-modify-машинерия Ouroboros (BIBLE.md, identity.md, scratchpad, knowledge tools, tools section, safety mechanics) **сохраняется без изменений** — она работает как внутренняя инфраструктура для git-операций / refactor'ов self.

## Что в этом PR

```
prompts/SYSTEM.md                         # PREPEND — новая секция в начале (~95 строк)
                                          #   self-modify mechanics ОСТАЮТСЯ ниже без правок
docs/adr/0017-system-prompt-analyst.md    # этот документ
docs/changelog/2026-05-07-system-prompt-analyst.md
tests/test_system_prompt_smoke.py         # NEW — regex-проверки что блок есть и читается
```

Ничего больше не правится — ни Ouroboros internals, ни skill, ни graph.

## Аргументация — главные неочевидные решения

### Почему prepend, а не replace

`prompts/SYSTEM.md` — 883 строки. Содержит критическую machinery:
- Tool protocols (read/write/shell/git, claude_code_edit safety guards).
- Safety Agent rules (sandbox, policy LLM checks, post-execution revert).
- Memory and Context (scratchpad, identity.md, knowledge_*, registry).
- Decision Gate (answer or delegate).
- Drift Detector (anti-reactivity, anti-task-queue-mode).
- Versioning (Bible Principle 9).
- Files and Paths.

Replace — это переписать ~80% файла с риском поломать что-то из вышеперечисленного. Prepend — добавить 95 строк в начало, всё остальное **гарантированно** работает как и раньше. Обратимо одним rebase'ом.

### Почему identity «I am Ouroboros» осталась

Artistically странно — снаружи мы аналитик, внутри «I am Ouroboros». Но семантически:
- Ouroboros mechanics (self-improve, knowledge_write, scratchpad) — реальная инфраструктура для разработчиков, **не доменная роль**.
- Identity как **becoming personality** — это для Ouroboros tool'ов (knowledge_*, identity.md, scratchpad). Без неё `update_identity()`/`update_scratchpad()` теряют смысл.
- Доменная роль аналитика **превалирует** над identity при выборе tool'а — мы это явно объявили в начале prompt'а.

Полное переписывание identity Ouroboros — это `feature/full-identity-rebrand`, отдельный PR, потенциально breaks много связанной machinery (BIBLE.md, ARCHITECTURE.md, knowledge-base topics). За дедлайн 5 дней — overkill.

### Почему секция в начале, а не в `## Tools`

Промпт читается LLM'ом сверху вниз. **Главное правило** должно быть в начале, не в middle. Размещение в `## Tools` — секция выглядит как enumeration tool'ов, инструкция теряется.

Prepend гарантирует, что LLM **прочтёт** доменную роль и tool routing **до** Ouroboros' identity-секции. Это работает как override: identity внутри (для self-improve механики), доменная роль снаружи (для пользовательских запросов).

### Почему конкретные списки «когда вызывать / когда не вызывать»

LLM лучше следует **explicit triggers**, чем размытым правилам. «На вопросы про нефть/газ» — vague. «На запрос «прогноз Brent / WTI / Urals / TTF / Henry Hub на N месяцев»» — explicit, LLM матчит лучше.

Добавление списка edge cases (Bonny Light, Sokol, JKM Asian LNG) — отражает 100-датасет в `intent_classifier.md`. LLM знает, что у нас покрытие на эти marки через proxy'и.

### Почему явные лимиты (RAG/web pending, shock-events, 2 edge cases) в промпт

Tool вернёт synthesis с дисклеймерами автоматически (через `nefteboros/prompts/synthesize_forecast_only.md`). Но **внешний** агент после tool-call'а должен:
- Не редактировать дисклеймер (LLM любит «улучшать» формулировки).
- Не пересказывать число без citation (LLM любит компилировать).
- Знать про known edge cases — чтобы переспрашивать creator'а если ответ выглядит странно.

Эти инструкции **дополняют** synthesis prompt в графе и закрывают gap'ы между tool output и финальным ответом creator'у.

### Почему не разделили на два файла

Альтернатива: создать `prompts/ANALYST.md` с доменной ролью, дополнить `context.py:683` чтобы загружал оба файла. Это:
- Правка `ouroboros/context.py` — extra surface, риск поломать context-build pipeline.
- Test-coverage для loader'а — ещё работа.
- Версионирование — два файла надо синхронизировать.

Простая prepend в существующий файл — minimal invasive, atomic, обратимо.

## Последствия

**Плюсы:**
- **Закрытое известное ограничение** v1.0.0 (identity «I am self-modifying»).
- Default systemprompt теперь явно направляет агента к `analyst_query` на нефтегазовые вопросы — closes loop с PR #12.
- Identity Ouroboros'а (для self-improve mechanics) сохранена.
- Минимальное изменение surface — 95 строк prepend'а, 0 правок Ouroboros internals.
- ADR-0013 §«Constraints for SKILL.md» правила теперь в трёх местах: rule-based code (graph), LLM disambiguate prompt, system prompt routing — defence in depth.

**Минусы / риски:**
- Identity-противоречие (снаружи аналитик, внутри Ouroboros) — концептуально странно для пользователя, читающего полный systemprompt. На demo creator-вопросы это не повлияет (LLM смотрит в начало промпта в первую очередь), но если кто-то попросит «расскажи о себе» — ответ может быть смешанный. Mitigation: в случае «расскажи о себе» можно добавить override в `_TOOL_DESCRIPTION` или в этот же блок (отдельный PR при необходимости).
- **Раздувание контекста** на ~1000-2000 токенов в каждом запросе. На GigaChat-Max / kimi-k2 — ~$0.0001 за токен, незначительно. На частых запросах — заметно, но это price of correctness.
- LLM может **случайно проигнорировать** правило и ответить по памяти — особенно на нерегулярных формулировках. Митигировано через explicit trigger lists + analyst_query сам ловит no_keyword_match через GigaChat. Defense in depth.

**Митигации:**
- Real-LLM smoke на сервере после deploy — проверить что агент вызывает `analyst_query` на golden-questions из ТЗ §4.6.
- Smoke-test (этот PR) — regex проверка, что блок есть и читается без поломок.
- Если в production окажется что LLM игнорирует — **дополнительный prefix** через `_TOOL_DESCRIPTION` в `analyst_query` (через extension namespace) — отдельный PR.

## Что НЕ в этом PR (явно)

- **Полная замена identity** Ouroboros'а на аналитика — `feature/full-identity-rebrand`, отдельный PR. Объём огромный (BIBLE.md, ARCHITECTURE.md, knowledge topics).
- **Удаление Consciousness/Evolve UI кнопок** в `web/` — `feature/ui-rebrand`, отдельный PR. Web фронт всё ещё показывает Ouroboros lore.
- **Перевод system prompt'а на английский / русский смешанный** — нюанс tone'а; сейчас English mechanics + Russian analyst block. Если creator на demo пишет по-русски, ответы LLM в основном по-русски — тестировать после deploy.
- **A/B-тест разных формулировок analyst block'а** — оптимизация tone'а / triggers через golden-eval с креатор-questions. Отдельный PR `feature/system-prompt-tuning` после telemetry.
- **Override identity-секции для «расскажи о себе» вопросов** — если в demo возникнут запросы про identity, отдельный PR.

## Альтернативы рассмотренные

- **Полная замена `prompts/SYSTEM.md`** — отвергнута: 883 строки, риск поломать tool/safety/memory mechanics. За 5 дней до дедлайна — overkill.
- **Создать `prompts/ANALYST.md` + правка `context.py` для двух-файлового loader'а** — отвергнута: extra surface для тестов и версионирования; добавляет работу не в core flow.
- **Override через `_TOOL_DESCRIPTION` в `analyst_query`** — отвергнута: tool description видит LLM при tool selection, **не** при initial systemprompt parsing. Не решает «когда вообще выбирать tool» проблему.
- **Использовать identity.md `update_identity()`** — отвергнута: identity.md — runtime self-update механика Ouroboros, регенерируется агентом на ходу. Не подходит для статичной доменной роли.

## Ссылки

- ADR-0001: [docs/adr/0001-fork-ouroboros.md](0001-fork-ouroboros.md) — форк Ouroboros и доменная адаптация
- ADR-0014/0015/0016: graph + LLM disambiguate + skill wrapper
- ADR-0013 §«Constraints for SKILL.md» — 5 правил, теперь и в этом prompt
- `docs/experiments/intent_classifier.md` — 0.98 type accuracy на 100-датасете
- `docs/architecture.md` — high-level схема
- Ouroboros context loader: `ouroboros/context.py:683` (`safe_read("prompts/SYSTEM.md")`)
- Известное ограничение: project memory «identity ещё «I am a self-modifying AI agent»» (закрыто этим PR на уровне primary mission, не identity).
