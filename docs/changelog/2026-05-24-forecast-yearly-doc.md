# 2026-05-24 — Прогноз цен на год вперёд по всем активам (документ + артефакт)

**Ветка:** `feature/forecast-yearly-doc`
**Связано:** [ADR-0024 — OU regime forecast](../adr/0024-ou-regime-forecast.md), [docs/report/forecast-section.md](../report/forecast-section.md), `nefteboros/forecast/scenarios.py` (ASSET_PARAMS snapshot 2026-05-08).

## Задача

Свести в один документ прогнозы цен на год вперёд (12m) по **всем** активам при текущей замороженной конфигурации модели (ASSET_PARAMS, snapshot `AS_OF_DATE=2026-05-08`), через публичную точку входа `forecast()`. Это не новая модель и не рекалибровка — снимок того, что движок выдаёт «как есть» сегодня.

## Контекст

- Прогнозный движок — regime-conditioned Ornstein-Uhlenbeck per scenario (ADR-0024), даёт **bounded CI** на длинных горизонтах. Проверено фактически: `forecast(asset, "12m", scenario="base")` возвращает **результат, не refusal** (legacy-отказ на 12m из ADR-0023 снят; refusal остаётся только на ≥18m и для неоткалиброванных активов).
- Задача не зависит от параллельной работы над flags→μ: берётся текущий дефолт `forecast()`.

## Что сделано

**Код:**
- Добавлен [`scripts/forecast_table.py`](../../scripts/forecast_table.py) — CLI-дамп: зовёт публичный `forecast()` по `ASSET_PARAMS` (10 активов) × {base, bear, bull} × {1m, 3m, 6m, 12m}, плюс одна строка `opec_basket` (демонстрация refusal). Собирает таблицу (spot, μ(t)-target, mid, CI80 low/high, CI95 low/high) и пишет csv + json с метаинформацией прогона (generated_at, git commit, версия Python, as_of, staleness-флаг). Per-combo try/except — флапающий live-фетч одного актива не валит весь прогон. Bootstrap (ROOT в sys.path, load_dotenv, фильтр warnings) — как в `scripts/eval/eval_ou.py`.

**Артефакт (point-in-time):**
- [`docs/report/forecast-table.json`](../report/forecast-table.json) и [`forecast-table.csv`](../report/forecast-table.csv) — 121 строка (120 ok + 1 refusal), сгенерировано 2026-05-24 на Python 3.12.12.

**Docs:**
- Расширен [`docs/report/forecast-section.md`](../report/forecast-section.md) — добавлен раздел «Прогноз на год вперёд — все активы»: таблица 12m по 10 активам × 3 сценария (mid + CI80), честная оговорка про устаревший snapshot, чтение по сценариям (включая INVERTED-bull для российского нефтегаза: тот же oil-bull сценарий = −26…−29% для GAZP/NVTK/MOEX O&G), строка про refusal `opec_basket`, инструкция воспроизведения. Существующая методология (OU vs SARIMAX, калибровка, backtest) **не дублирована** — только ссылки.

## Обязательная оговорка про snapshot (выполнена в документе)

Сегодня 2026-05-24, `AS_OF_DATE=2026-05-08` → **16 дней** > `REVIEW_AFTER_DAYS=14` → snapshot потенциально устарел. В документе это указано явно: μ заморожены под «Brent ~$100», а live spot уже $103.54 (obs 2026-05-22). Отмечено, что после этапа 2 (web→flags) таблицу можно регенерить с актуальными флагами.

## Файлы

- **Добавлено:** `scripts/forecast_table.py`, `docs/report/forecast-table.json`, `docs/report/forecast-table.csv`, этот changelog.
- **Изменено:** `docs/report/forecast-section.md` (новый раздел перед «Walk-forward результаты»).
- **Удалено:** —

## Что НЕ в PR (non-goals, осознанно)

- **Модель/калибровка не тронуты** — `ASSET_PARAMS` без изменений.
- **Новый бэктест не делался** — walk-forward в `scripts/eval/eval_ou.py`, цифры взяты как есть.
- **Новый ADR не заводился** — архитектурного решения нет, потребляется существующий путь ADR-0024.
- **flags→μ** (этап 2) — отдельная ветка; дефолт `forecast()` здесь не меняется.

## Слабые места (честная разметка)

1. **Числа не воспроизводимы побитово.** mid/CI зависят от live spot на момент прогона; перезапуск завтра даст другой spot (особенно на коротких горизонтах, где вес spot ≈ exp(−θt) велик). Mitigation: артефакт самодатируется (generated_at + spot + obs_date в шапке json), документ помечен point-in-time. Это следствие выбора публичной точки входа `forecast()` (как в ТЗ), а не `compute_ou_forecast` с фиксированным spot.
2. **Расположение артефакта — `docs/report/` рядом с документом**, а не `metrics/runs/<date>_<component>_<commit>.json` по конвенции метрик. Выбор: артефакт — это данные под отчёт, не eval-метрика, и кликается из документа. Если нужнее конвенция metrics/runs — переезд тривиален (флаг `--out-dir`).
3. **Окружение прогона — минимальный 3.12-venv** (yfinance/pandas/numpy/pydantic/dotenv), не полный domain-install. forecast-путь не импортирует langchain/gigachat/torch (проверено), поэтому числа идентичны прод-окружению (та же арифметика OU + те же данные yfinance). Но это не буквально прод-env.
4. **MOEX-spot разной свежести:** moexog obs 2026-05-22, а GAZP/NVTK obs 2026-05-24 — MOEX ISS вернул разные последние торговые строки. Минорно, отражено в obs_date по строкам.

## Воспроизведение

```
uv venv -p 3.12 .venv312 && uv pip install --python .venv312 yfinance pandas numpy pydantic python-dotenv
.venv312/bin/python scripts/forecast_table.py
```
