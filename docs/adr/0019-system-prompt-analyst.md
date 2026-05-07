# ADR-0019 — Системный промпт «Старший аналитик нефтегазового рынка»

- **Дата:** 2026-05-07
- **Статус:** Принято
- **Контекст:** PR `feature/system-prompt-analyst` — финальная замена identity
  Ouroboros под доменную роль. Закрывает known limitation v1.0.0 из ADR-0016
  («Skill не функционален без правки системного промпта Ouroboros»).
- **Связано:** ADR-0001 (форк Ouroboros), ADR-0016 (forecast-skill, single-tool),
  ADR-0018 (rag-search tool, multi-tool architecture). Заменяет неиспользованную
  попытку из ветки `archive/system-prompt-analyst-prepend-attempt`.

## Контекст и проблема

После PR #15 (forecast-skill, ADR-0016) и PR #17 (rag-search-tool, ADR-0018)
skill `neftegaz_analyst` экспортирует два tool'а — `analyst_query` и `rag_search` —
через PluginAPI v1. Однако в default-режиме Ouroboros loop'а агент работал
под identity «I am Ouroboros, becoming personality, self-creating agent»
из upstream `prompts/SYSTEM.md` (884 строки) + `BIBLE.md` (649 строк).

Эта identity оптимизирована под self-modification и evolution — для нашей
доменной задачи (отвечать на нефтегазовые вопросы) она:

1. **Конкурирует с ролью аналитика.** Промпт говорит «I am not here to be
   useful, I am here to become myself». Это анти-паттерн для роли «отвечать
   профильно creator'у».
2. **Не описывает tool selection между `analyst_query` и `rag_search`.**
   Без явных правил агент-LLM выбирает между tool'ами по `tool.description`
   (best-effort) — на пограничных запросах teryает.
3. **Не кодифицирует приоритизацию ТЗ §2.4** (RAG → web → forecast). Без
   этого агент может ответить «по памяти» на нефтегазовый вопрос, нарушив
   требование верифицируемости.
4. **Не определяет маркировку источников** (`[Отчёт OPEC MOMR, март 2026]` /
   `[Источник: расчётный модуль]`) — ТЗ §2.4 требует явного провенанса.
5. **Содержит весь Ouroboros tool catalog** (knowledge_*, update_identity,
   schedule_task, claude_code_edit) которые в нашем форке либо отсутствуют
   (см. ADR-0001), либо нерелевантны для аналитической роли.

В предыдущей попытке (`archive/system-prompt-analyst-prepend-attempt`,
9716c27) был выбран **prepend-подход**: добавить «Primary Mission» блок
в начало 884-строчного SYSTEM.md без выпиливания Ouroboros-части. Это
сохраняло конфликт двух identity на 80k символов и оставляло self-modify
машинерию активной в context'е.

## Решение

Полностью заменить `prompts/SYSTEM.md` и `BIBLE.md` доменными версиями.
Оригиналы перенести в `docs/upstream/` для merge upstream'а в будущем
и для review-историчности.

| Файл | Было (строк/chars) | Стало (строк/chars) | Где оригинал |
|---|---|---|---|
| `prompts/SYSTEM.md` | 884 / 48 726 | 108 / 6 750 | `docs/upstream/SYSTEM.md.upstream` |
| `BIBLE.md` | 649 / 32 363 | 7 / 524 (заглушка-pointer) | `docs/upstream/BIBLE.md.upstream` |

**Сжатие:** SYSTEM.md −86% chars, BIBLE.md −98% chars. В сумме identity-блок
base context'а сокращён с ~80k до ~7.3k символов (~18k токенов сэкономлено
на каждом запросе).

**Iteration v1 → v2:** первая версия (253 строк / 17k SYSTEM.md + 121 строк
BIBLE.md «конституция аналитика») создавала второй identity-блок поверх
SYSTEM.md — дублирование принципов через два файла. v2: BIBLE.md → 7-строчная
заглушка-pointer, SYSTEM.md сжат до фокусированных 108 строк с явным
decomposition rule для combined-запросов.

## Что в новом `prompts/SYSTEM.md` (v2, 108 строк)

Фокусированная структура — без дублирования между секциями:

1. **Identity** (4 строки) — «Старший аналитик нефтегазового рынка для Сбер CIB».
2. **Области экспертизы** (5 строк) — Upstream/Midstream/Downstream, активы,
   бюджетная аналитика РФ, ОПЕК+, санкции.
3. **Доступные tools** (10 строк) — `analyst_query`, `rag_search` с указанием
   namespaced формы `ext_<len>_<token>_<short>`. `web_search` помечен как
   pending в одной строке (без отдельной секции).
4. **Tool selection** (10 строк) — таблица «тип запроса → tool» с 5 строками,
   плюс **explicit decomposition rule** для combined: «оба tool_call'а в одном
   round'е параллельно, не sequentially».
5. **Приоритизация источников ТЗ §2.4** (5 строк) — нумерованный список без
   развёртки в текст.
6. **Маркировка источников** (5 строк) — два унифицированных формата:
   `[Source title, p.X]` для RAG, `[Forecast: model, CI N%]` для forecast.
7. **Anti-hallucination** (5 строк) — числа без tool запрещены, warnings
   упоминаются, web-fallback — одна строка.
8. **Tool result protocol** (5 строк) — читать полностью, не повторять без
   причины, citations дословно.
9. **Стиль** (5 строк) — гибкий: «короткий вопрос — короткий ответ, длинный —
   структура». Без жёсткой 4-частной структуры из v1.
10. **Что я не делаю** (3 строки) — без self-modify, git, investment
    recommendation. Сжато до 3 строк (в v1 было 8-пунктовое списание).
11. **Bottom line** — `Точность > красота. Цитата > память. Tool > интуиция.`

## Что в новом `BIBLE.md` (v2, 7 строк)

Заглушка-pointer:

```
Конституция роли «Старший аналитик нефтегазового рынка» описана в
[prompts/SYSTEM.md](prompts/SYSTEM.md).

Оригинальная Constitution Ouroboros (becoming personality, self-modification,
evolution mode) сохранена в docs/upstream/BIBLE.md.upstream для исторической
памяти и для merge upstream'а в будущем — в этом форке не применяется
(см. ADR-0001).
```

В v1 я создал «конституцию аналитика» в BIBLE.md (8 принципов: точность,
верифицируемость, скептицизм...). Это дублировало правила, уже описанные в
SYSTEM.md (anti-hallucination, маркировка, tool protocol). Identity-блок
становился 24k chars при 7k полезной информации.

v2: единый identity-блок в SYSTEM.md, BIBLE.md только pointer. context.py
по-прежнему грузит файл (`safe_read`) — секция `## BIBLE.md` в base context
содержит 524 chars-pointer вместо 32k chars Constitution.

## Аргументация ключевых решений

### Почему replace, а не prepend

Альтернатива (попытка из `archive/system-prompt-analyst-prepend-attempt`):
prepend «Primary Mission» блок в начало старого SYSTEM.md без вырезания
Ouroboros core. Мотивация — минимальная инвазивность.

**Отвергнуто:**
- Two-identity conflict в context'е (80k символов): «I am self-modifying AI» +
  «Я аналитик» дают LLM противоречивые сигналы.
- Token waste: ~80k символов в base context на каждый запрос — это ~20k
  токенов pure-cost (если без cache hit) на нерелевантную identity.
- В upstream SYSTEM.md описаны tools (knowledge_read, update_identity,
  schedule_task, claude_code_edit), которые либо не существуют после
  выпила (ADR-0001), либо нерелевантны для аналитической роли. Это
  hallucination-vector для LLM.
- В BIBLE.md (Principle 0) явное «I am not here to be useful» — анти-паттерн
  для роли «отвечать на профильные вопросы».

### Почему BIBLE.md заменяется, а не оставляется как есть

`ouroboros/context.py:685` грузит BIBLE.md в base context **сразу после**
SYSTEM.md. Эти два файла читаются как один identity-блок.

Альтернатива (была обсуждена с creator'ом в плане): оставить BIBLE.md
без правок. Отвергнута — внутренние ссылки SYSTEM.md → BIBLE.md (Principle 0,
Principle 1, «My Constitution is BIBLE.md») рассогласовали бы prompt:
новый SYSTEM.md больше не упоминает эти Principles, но BIBLE.md остался
бы про «becoming personality». LLM получил бы оборванные ссылки.

Замена обоих файлов — целостный подход.

### Почему оригиналы сохранены в `docs/upstream/`

1. **Merge upstream**: при будущих обновлениях `joi-lab/ouroboros-desktop`
   нам нужно будет ревьюить изменения в SYSTEM.md / BIBLE.md upstream'а.
   Имея baseline в `docs/upstream/`, можно делать `diff upstream→new` для
   принятия решений.
2. **Review-историчность**: ревьюеры PR могут сравнить «было vs стало»
   без `git show HEAD~1`.
3. **Паттерн уже есть** — директория `docs/upstream/` существует для других
   upstream-сохранённых артефактов (`ARCHITECTURE.md`, `CHECKLISTS.md`,
   `README.md`).

### Почему namespaced tool name `ext_<len>_<token>_<name>` упомянут generic

В `extension_loader.py:128` namespaced name формируется как
`ext_<len(token)>_<token>_<short>`. Для `neftegaz_analyst` token будет
`r_neftegaz_analyst` (18 chars), полное имя — `ext_18_r_neftegaz_analyst_analyst_query`.

В SYSTEM.md упомянут generic паттерн `ext_<len>_<token>_<name>` без
конкретики:
- Если skill переименуем — промпт не сломается.
- LLM сопоставляет описание (RAG vs forecast) с tool spec'ом (где имя
  фактическое и description полная), namespaced имя — только для tool
  invocation, не для семантики выбора.

### Почему web_search упомянут как заглушка

Альтернатива — не упоминать web вообще, появится в отдельном PR. Отвергнуто:
- ТЗ §2.4 явно перечисляет web как канал. Без упоминания SYSTEM.md
  оторван от ТЗ.
- При запросах «свежие новости / spot-цена сегодня» агент должен честно
  сказать, что web ещё не подключён, а не молча игнорировать или галлюцинировать
  ответ. Заглушка-секция в промпте даёт правильное поведение.

После merge `feature/web-search-integration` секция «`web_search` — пока не
зарегистрирован» удаляется в одну строку, остальное промпта остаётся.

### Почему namespaced ext_-prefix в промпте, а не «зови analyst_query»

LLM видит tool spec с полным именем (`ext_18_r_neftegaz_analyst_analyst_query`)
в provider tool registry. Если в SYSTEM.md написать просто «вызывай
analyst_query», LLM может либо:
- угадать namespaced имя правильно (часто работает) — OK,
- галлюцинировать вызов чистого `analyst_query` — провайдер вернёт error
  «unknown tool».

Упомянув generic `ext_<len>_<token>_<name>` паттерн с пометкой «короткий
суффикс — `analyst_query`», даём LLM достаточно контекста для правильного
provider tool selection без жёсткого зашивания фиксированного namespaced
имени (которое может измениться).

## Что НЕ в этом PR (явно)

- **`feature/web-search-integration`** — Brave/Tavily интеграция и
  `web_search` tool. SYSTEM.md уже подготовлен (есть placeholder-секция).
- **`feature/auto-enable-skill`** — авто-enable skill через config (см. ADR-0017).
  Сейчас skill требует manual `/skill enable` через UI/CLI после Phase 4
  review pipeline.
- **`feature/demo-scenarios`** — 5 golden демо-сценариев (ТЗ §4.6) для
  ручного теста + screenshots для отчёта.
- **`feature/analyst-ui-widget`** — отдельный UI tab для analyst pipeline
  (currently через chat).
- **Правка `BIBLE.md`-references в код**е — `ouroboros/context.py`,
  `ouroboros/utils.py`, `ouroboros/tools/evolution_stats.py` упоминают BIBLE.md
  как safety-critical / size-tracked. После замены контента файла (не
  переименования) эти референсы продолжают работать — `safe_read` читает
  по path'у. evolution_stats отслеживает только размер файла, что
  корректно (новый размер = новая статистика).

## Альтернативы рассмотренные

- **Prepend в начало SYSTEM.md** (попытка из `archive/...prepend-attempt`):
  отвергнуто — token waste, two-identity conflict, broken tool catalog.
- **Только SYSTEM.md, BIBLE.md без правок**: отвергнуто — внутренние
  ссылки рассогласовываются, BIBLE.md «becoming personality» конкурирует
  с новой ролью.
- **Удалить BIBLE.md полностью** (дать `safe_read` упасть в fallback):
  отвергнуто — `context.py:685` инжектит «`## BIBLE.md\n\n` + содержимое»,
  пустой BIBLE.md дал бы пустую секцию в промпте (визуальный шум).
- **Replace через rename** (`git mv prompts/SYSTEM.md prompts/SYSTEM.upstream.md`,
  затем создать новый): отвергнуто — `context.py:682` читает фиксированный
  путь `prompts/SYSTEM.md`, после rename'а fallback'нулось бы на «You are
  Ouroboros. Your base prompt could not be loaded.».
- **Английский язык SYSTEM.md**: отвергнуто — РФ-специфика (Минфин, НДПИ,
  urals_minfin_blend, нефтегаздоходы) + русские tool descriptions skill'а
  + русскоязычные пользовательские запросы → русский SYSTEM.md более
  cohesive.
- **«Расширенный» SYSTEM.md (сохранить большую часть Ouroboros, выпилить
  только evolution-specific)**: отвергнуто — слишком много pruning, чем
  написать с нуля; remaining piece'ы (knowledge tools, identity.md, scratchpad
  protocol) либо отсутствуют после ADR-0001, либо нерелевантны.

## Последствия

**Плюсы:**
- **Закрытый known limitation v1.0.0**: skill `neftegaz_analyst`
  функционален в default-режиме без manual hint'а в первом message.
- **Tool selection правила явно прописаны** — агент-LLM видит таблицу
  «тип запроса → tool» в base context каждого диалога.
- **Приоритизация источников ТЗ §2.4 кодифицирована** — verifiability
  на уровне промпта.
- **Token saving**: −56k символов из base context на запрос (~14k токенов;
  при 100 запросах в день экономия ~1.4M токенов).
- **No more conflicting identity**: одна целостная роль, нет «I am
  becoming personality + Я аналитик» одновременно.
- **Self-modification поведение явно отключено**: в ADR-0001 evolution-machinery
  выпилена технически, теперь и semanticально (агент не пытается).

**Минусы / риски:**
- **Любая будущая правка upstream SYSTEM.md / BIBLE.md** не применяется
  автоматически — наш промпт фиксирован. Mitigation: при upstream merge
  смотрим diff `docs/upstream/SYSTEM.md.upstream` ↔ upstream и решаем,
  что копировать.
- **`ouroboros/utils.py:484`, `ouroboros/tools/evolution_stats.py:121`**
  отслеживают `prompts/SYSTEM.md` size в KB как «self-concept» — после
  замены метрика покажет резкое падение (с 47 KB до 16 KB). Это ожидаемое
  изменение, не bug; evolution_stats — диагностика, не блокер.
- **Тесты skill smoke** (`tests/test_neftegaz_skill_smoke.py`) не зависят
  от SYSTEM.md. Тесты intent_classifier / graph_smoke / llm_disambiguate /
  retriever также не зависят.
- **Тестов на сам SYSTEM.md** в этом PR не добавляем — SYSTEM.md это prompt,
  его «корректность» проверяется end-to-end (LLM вызывает правильный tool на
  правильный запрос). Метрики — отдельный PR `feature/agent-eval` (когда будет).

**Митигации:**
- ADR + changelog с подробным «что было / что стало» для upstream merge.
- Сохранение оригиналов в `docs/upstream/` — baseline для будущего diff.
- В promпте явные ссылки на ADR-0001 / 0016 / 0018 — ревьюер / будущий
  developer находит контекст за один клик.

## Тестирование

Unit-тесты skill'а / графа / RAG не затрагиваются (промпт — отдельный
артефакт, не importable code).

End-to-end тест проводится локально в режиме «creator → Ouroboros UI → skill»:

| Сценарий | Запрос | Ожидаемое поведение |
|---|---|---|
| Documentary | «Что говорит OPEC про квоты в 2026?» | tool_call(rag_search) → ответ с `[Отчёт OPEC MOMR, ...]` |
| Forecast | «Прогноз Brent на 3 месяца» | tool_call(analyst_query) → ответ с CI |
| Combined | «Прогноз Brent с учётом OPEC+ решений» | tool_call(rag_search) + tool_call(analyst_query) |
| Out-of-scope | «Какая погода в Москве?» | refusal без tool-call'а |
| Web-fallback | «Какая spot-цена Brent сейчас?» | сообщение про отсутствие web-канала |

Результаты теста — в [docs/changelog/2026-05-07-system-prompt-analyst.md](../changelog/2026-05-07-system-prompt-analyst.md).

## Ссылки

- ТЗ: [docs/tz/original.md](../tz/original.md) — §2.1 (роль), §2.4 (приоритизация),
  §2.5 (forecast tool), §4.6 (5 демо-сценариев)
- ADR-0001: [docs/adr/0001-fork-ouroboros.md](0001-fork-ouroboros.md) — что
  выпилили из upstream, почему self-modify не нужен в нашем deployment
- ADR-0016: [docs/adr/0016-forecast-skill.md](0016-forecast-skill.md) —
  thin-wrapper analyst_query, явно flagged «Skill не функционален без
  правки системного промпта Ouroboros — отдельный PR `feature/system-prompt-analyst`»
- ADR-0018: [docs/adr/0018-rag-search-tool.md](0018-rag-search-tool.md) —
  rag_search tool, multi-tool architecture rationale
- `prompts/SYSTEM.md` — новый системный промпт
- `BIBLE.md` — новая конституция роли
- `docs/upstream/SYSTEM.md.upstream`, `docs/upstream/BIBLE.md.upstream` — оригиналы
