USER QUERY:
{{QUERY}}

CLASSIFIED INTENT:
{{INTENT}}

FORECAST RESULTS (JSON; элементы — ForecastResult или ForecastRefusal):
{{FORECAST_RESULTS_JSON}}

FORECAST ERRORS (активы, прогноз которых не удалось получить):
{{FORECAST_ERRORS}}

ЗАДАЧА:

Сформируй ответ для пользователя на основе FORECAST RESULTS:

1. Кратко переформулируй запрос («Прогноз X на Y») — одна строка.

2. Для каждого ForecastResult из списка:
   - Базовая оценка (`points[-1].value`) с единицей измерения (см. metadata).
   - CI 80% (`points[-1].ci_80.low`–`points[-1].ci_80.high`).
   - CI 95%, если запрос требует широкого диапазона.
   - Метод (`method`).
   - Если `backtest_summary` не null — упомяни MASE vs RW и coverage_80
     для последнего регима (iran_2026 / cap_normalization / aggregate).

3. Для каждого ForecastRefusal:
   - Объясни (используя `reason`), почему точечный прогноз не делается.
   - Перечисли redirect_to-источники (WOO 2025 / IEA Oil 2025 / ИНЭИ /
     Энергостратегия РФ-2050).

4. Если активов несколько (forecast_with_context — РФ-контекст:
   brent + urals + urals_minfin_blend):
   - Сравнительная сводка: спред Brent–Urals, реалистичность
     Минэк-формулы 0.78×Urals + 0.22×ESPO.

5. Если FORECAST_ERRORS не «(нет)» — упомяни какие активы и почему
   не прогнозируются.

6. **ОБЯЗАТЕЛЬНО** в конце ответа добавь блок:

   > Для production-аналитики этому ответу нужен overlay из RAG
   > (сценарии OPEC WOO 2025, IEA Oil 2025, CRS Iran 2026, Бруэгель
   > WP 32/2025, Энергостратегия РФ-2050) и web-search (свежие новости,
   > заявления OPEC+, futures-curve indicators). Эти источники появятся
   > в следующих PR'ах. Текущий ответ — base-case из стат-моделей,
   > не учитывает геополитические шоки и текущий новостной фон.

7. После disclaimer'а — список ссылок:
   - `[forecast_model:<asset>@<horizon>, <method>, ADR-0012]`
     для каждого ForecastResult.
   - `[forecast_refusal:<asset>]` для ForecastRefusal.

ВНИМАНИЕ:

- Используй ТОЛЬКО данные из FORECAST_RESULTS и FORECAST_ERRORS.
- Не выдумывай числа, метрики, источники, новости.
- Не сокращай число знаков после запятой агрессивно — финансовые цифры
  важны (USD/bbl до 2 знаков, EUR/MWh до 2, USD/MMBtu до 2, RUB до 2).
