# 2026-05-25 — Regression test для изоляции диалогов E2E

## Задача

Защитить регресс-тестом фикс tail-timeout из PR #78 / ADR-0027 (изоляция
диалогов в WSRunner). Детерминированный proof механизма (`build_llm_messages`
18k→141k→18k) в том PR был описан, но не закоммичен как тест — фикс остался
незащищён от тихой регрессии.

Follow-up по итогу PR #78 (по запросу координатора). Не блокер, отдельный
мелкий PR.

## Что сделано (тест)

`tests/test_eval_dialogue_isolation.py` — чистый unit (без LLM/сервера/сети,
0.4s):

1. `test_recent_chat_section_grows_with_history` — `build_recent_sections`
   инжектит `## Recent chat` из `chat.jsonl`, секция растёт с числом записей;
   при пустом `chat.jsonl` секции нет.
2. `test_context_grows_with_history_and_resets_on_clear` — контекст
   (`build_llm_messages` estimated_tokens) монотонно растёт с историей
   (`base < mid < big`, `big > base*2`), а очистка `chat.jsonl` (что делает
   `POST /api/chat/clear`) возвращает к базе.
3. `test_http_base_from_ws` — деривация HTTP-эндпоинта из ws-url.
4. `test_isolation_gate_skips_clear_when_disabled` — `EVAL_CHAT_ISOLATION=0`
   → `_clear_chat_history` не делает HTTP-вызова.

Тесты #1–2 стерегут инвариант, на котором держится фикс: если сборка контекста
изменится так, что история перестанет инжектиться/сбрасываться, тест упадёт и
заставит пересмотреть необходимость изоляции в eval.

## Что НЕ в PR

Поведенческих изменений нет — только тест. Бэклог (#2 cost main-loop, #3 flush,
orphan traces, оптимизация базового контекста) — без изменений.

## Файлы

- Добавлено: `tests/test_eval_dialogue_isolation.py`, этот changelog.

## Связанное

ADR-0027 (eval-dialogue-isolation), PR #78, changelog
2026-05-24-observability-eval-robustness.
