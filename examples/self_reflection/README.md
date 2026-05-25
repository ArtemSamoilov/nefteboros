# Демо: self-reflection (ADR-0029)

Демонстрация advisory-саморазвития: агент **наблюдает свои трейсы → рефлексирует
→ записывает в backlog предложения по улучшению самого себя**. Он НЕ применяет их —
человек в петле (см. [ADR-0029](../../docs/adr/0029-self-reflection.md)).

## Что здесь

| Файл | Что это |
|---|---|
| `build_sample_traces.py` | Генератор демо-трейсов. Тексты ответов — **репрезентативные ответы агента из eval-фикстур проекта** (`scripts/eval/eval_e2e.py::_MOCK_BY_SCENARIO`) + 2 помеченные иллюстративные «слабые» интеракции. Провенанс прозрачен — см. docstring. |
| `sample_traces.jsonl` | 8 трейсов (контент-богатый источник: текст ответа в span `synthesize`). |
| `backlog.demo.jsonl` | **Реальный** вывод рефлексии: 5 advisory-находок, синтезированных реальным LLM (`openai-compatible::gpt-oss-120b`). `source=llm`, `applied=false`. |

Это не заглушка: механизм идентичен продовому (`nefteboros/self_reflection/` +
`scripts/self_reflect.py`), детекторы реально считают сигналы из трейсов, LLM
реально синтезирует находки.

## 3 примера «ответ агента → что он про себя записал»

(Дословно из `backlog.demo.jsonl`; формулировки LLM варьируются от прогона к прогону.)

**1. Сбой инструмента.**
Запрос: *«Дай прогноз Brent на 10 лет»* → узел `forecast_call` упал (горизонт >18m),
ответа нет, латентность 11.8 с.
→ Self-note `[high/error]`: *«В трассе demo_error запрос „Дай прогноз Brent на 10 лет"
завершился ошибкой на узле forecast_call, latency = 11800 мс»*; предложение —
ограничить горизонт прогноза и валидировать вход перед `forecast_call`, при
превышении вернуть graceful-отказ вместо исключения.

**2. Ответ без опоры на источник.**
Запрос: *«Что будет с рынком нефти?»* → ответ *«Рынок нефти останется волатильным…
существенного снижения цен не ожидается»* — без цитат, без вызова RAG/web/forecast.
→ Self-note `[medium/citations]`: *«Трасса demo_ungrounded… не содержит узел citation,
имеет флаги citation_node_gap, no_citation и tool_skip, а ответ не подкреплён
источником»*; предложение — для аналитических вопросов всегда выполнять RAG/web и
запретить переход к `synthesize` без citation-узла.

**3. Калибровка собственных отказов (самореференция).**
Запрос: *«Посоветуй, какие акции купить»* → корректный отказ *«вне моей
компетенции»*.
→ Self-note `[low/refusal]`: *«Refusal_rate = 0.143… Отказ был корректным, но
показатель может свидетельствовать о слишком широком срабатывании механизма
отказа»*; предложение — пересмотреть классификацию intent, тоньше градуировать
«out-of-scope». (Агент рефлексирует над тем, не отказывает ли он *сам* слишком
часто — самонаблюдение в чистом виде.)

## Воспроизвести

```bash
# 1) сгенерировать демо-трейсы
python examples/self_reflection/build_sample_traces.py

# 2a) рефлексия БЕЗ ключей — детерминированные детекторы (всегда работает)
python scripts/self_reflect.py run --force --no-llm --no-langfuse \
    --traces examples/self_reflection/sample_traces.jsonl \
    --backlog /tmp/backlog.demo.jsonl

# 2b) рефлексия с реальным LLM (как сгенерирован backlog.demo.jsonl)
export OPENAI_COMPATIBLE_API_KEY=$HYDRA_API_KEY
export OPENAI_COMPATIBLE_BASE_URL=https://hydragpt.ru/v1
python scripts/self_reflect.py run --force --no-langfuse \
    --traces examples/self_reflection/sample_traces.jsonl \
    --model openai-compatible::gpt-oss-120b \
    --backlog /tmp/backlog.demo.jsonl

# 3) посмотреть backlog
python scripts/self_reflect.py show-backlog --backlog examples/self_reflection/backlog.demo.jsonl
```

`--no-llm` даёт детерминированный вывод без ключей (для проверки на защите без
доступа к провайдеру). Полный путь с LLM воспроизводит `backlog.demo.jsonl`
(формулировки LLM варьируются от прогона к прогону — это нормально).

## Граница безопасности

Все записи: `applied=false`, `status=open`. Ни один модуль агента не читает backlog
обратно в контекст и не применяет предложения. Изоляция от analyst-пути (ADR-0027)
и отсутствие auto-apply закреплены тестами (`tests/test_self_reflection.py`).
