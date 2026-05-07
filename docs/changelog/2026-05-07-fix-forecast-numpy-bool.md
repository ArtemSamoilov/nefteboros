# Changelog: fix(forecast) — `use_log` coerce numpy.bool → Python bool

- **Дата:** 2026-05-07
- **PR:** `feature/fix-forecast-numpy-bool`
- **Связанные:** ADR-0012 (forecast pipeline), ADR-0014 (analyst graph), PR #18 deploy

## Задача

После production deploy на 186.246.2.190 при первом forecast-запросе (UI: «какой
прогноз на цены газа через год?») агент показал:

```
PydanticSerializationError: Unable to serialize unknown type: <class 'numpy.bool'>
```

В логах сервера crash в `nefteboros/graphs/nodes/synthesize.py:61` —
`r.model_dump(mode="json")` падает на сериализации `state.forecast_results`.
Forecast tool корректно посчитал прогноз, но synthesize узел не смог упаковать
его в LLM prompt → агент получил error через graceful path и (по нашему промпту
из PR #18) честно сообщил о технической проблеме без галлюцинации цифр.

**Промпт сработал идеально** — anti-hallucination правило не дало выдумать
прогноз на ошибке tool'а. Но сам forecast pipeline блокирует ТЗ §2.5
(прогнозирование цен).

## Корневая причина

`nefteboros/forecast/api.py:163`:

```python
use_log = meta.log_transform and (history > 0).all()
```

`history` — `pandas.Series`. `(history > 0).all()` возвращает **`numpy.bool_`**
(не Python `bool`). Python `and` оператор возвращает второй операнд как есть
если первый truthy → `use_log` оказывается `numpy.bool_`.

В pydantic 2.x + numpy 2.x `numpy.bool_` **не сериализуется** в `mode="json"`:
у pydantic нет встроенного encoder'а для numpy типов. На сервере свеже-
установленный `numpy-2.4.4` (из `requirements-domain.txt`); локально у Артёма
возможно был numpy < 2.0 с менее строгой проверкой типов.

`use_log` затем попадает в `metadata={"log_transform_applied": use_log, ...}`
ForecastResult'а. При `model_dump(mode="json")` pydantic валится на этом поле.

## Решение

`bool(...)` coerce'нет к Python native:

```python
-    use_log = meta.log_transform and (history > 0).all()
+    use_log = bool(meta.log_transform and (history > 0).all())
```

Одна строка + поясняющий комментарий о том, почему `bool()` нужен (для
будущих читателей кода — иначе ловушка повторится).

## Что НЕ в PR

- **Полная защита от numpy типов в `metadata`** через `field_serializer` в
  `ForecastResult` — overkill для одного известного источника. Если в
  будущем появится ещё одно место — добавим.
- **Coercion в `synthesize.py`** через custom JSON encoder — тоже overkill;
  лучше фиксить в источнике.
- **Cleanup `derived_layer.py:124,204`** — там metadata тоже dict'ом
  собирается (`**urals_fc.metadata.items()`), но **тащит base.metadata**
  который уже coerce'ен после нашего fix'а. Spread* fields там — Python
  float (не numpy). Безопасно.

## Файлы

**Изменено:**

- `nefteboros/forecast/api.py:163` — одна строка + комментарий

**Добавлено:**

- `docs/changelog/2026-05-07-fix-forecast-numpy-bool.md` (этот файл)

ADR отдельный не пишу — это bugfix совместимости с numpy 2.x, не
архитектурное решение.

## Тесты

### AST

`nefteboros/forecast/api.py` — валиден.

### Smoke (live forecast → model_dump)

```python
result = forecast(asset='brent', horizon=Horizon.M3)
payload = result.model_dump(mode='json')
# Было: PydanticSerializationError
# Стало: payload['metadata']['log_transform_applied'] = False (type: bool)
```

Forecast рассчитан (ARIMA + Prophet ensemble), `model_dump(mode="json")`
прошёл без ошибки. Поле `log_transform_applied` теперь Python `bool`,
не `numpy.bool_`.

### Что не unit-тестировано

Тест на конкретное поле metadata в `tests/test_forecast.py` — следовало бы
добавить, но в этом PR scope только bugfix. Расширение test coverage —
отдельная задача.

## Deployment notes

После merge на сервер:

```bash
ssh -i ~/.ssh/id_ed25519_nefteboros root@186.246.2.190 "
  cd /root/nefteboros && \
  git pull --ff-only origin main && \
  systemctl restart nefteboros && \
  systemctl is-active nefteboros
"
```

Никаких pip / env / migration. После restart первый forecast-запрос через
UI должен пройти end-to-end (forecast pipeline → synthesize → LLM prompt
с числами + CI).

## Связанные

- ADR-0012 (`docs/adr/0012-price-tools.md`) — forecast pipeline
- ADR-0014 (`docs/adr/0014-langgraph-subgraph.md`) — synthesize node в графе
- ADR-0019 (`docs/adr/0019-system-prompt-analyst.md`) §«Anti-hallucination» —
  где промпт корректно отработал на этой ошибке
- PR #18 (deploy) — где баг проявился впервые на свежем numpy 2.x
