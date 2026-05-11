"""Cost estimation для LLM-вызовов.

Иерархия (см. ADR-0024-observability-langfuse §«Cost calculation»):

1. Наши захардкоженные ставки `COST_RATES` — для моделей, специфичных нашему
   стеку (kimi-k2p6 через Hydra, GigaChat-2-Max). Приоритет, потому что ouroboros
   не знает про эти провайдеры.
2. Fallback в `ouroboros.pricing.estimate_cost` — для OpenRouter-style моделей.
3. Если и там нет — `None` (span получает `cost_usd: null`, не 0).

Ставки в USD per 1M tokens (input, cached, output). Источники:
- Kimi-k2p6 на Hydra: https://hydragpt.ru/pricing (актуально на 2026-05-08).
- GigaChat-2-Max: https://developers.sber.ru/docs/ru/gigachat/api/tariffs
  (пересчитано из ₽ по курсу 92 ₽/$ — приближённо для dashboard'а; точные
  цифры в ₽ — у Сбера в LK).
- GigaChat-Max (предыдущая версия): аналогично.

При обновлении ставок — менять руками, в roadmap v2.2 рассмотреть выгрузку из
provider config / yaml (overengineering для спринта).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Курс ₽→$ для пересчёта тарифов GigaChat (которые в ₽ на стороне Сбера).
# Меняется на FX рынке ежедневно — для observability dashboard'а используем
# усреднённое значение, точный cost — в LK биллинга. При значимых изменениях
# курса (>10%) обновлять руками.
_RUB_USD_RATE = 92.0


# (input_per_1m_usd, cached_per_1m_usd, output_per_1m_usd)
# Если cached_rate==input_rate — провайдер не даёт кеш-скидку.
#
# Источники:
# - Hydra (Cloud.ru / JOI proxy): https://hydragpt.ru — закрытый API gateway
#   без public pricing. Ставки approximate, сверить в LK после первого
#   месяца использования.
# - GigaChat (Sber): официальные B2B-тарифы 2026-05, переданные Артёмом.
#   Сбер берёт одинаковую цену за input и output (cached_rate == input_rate
#   — кеш-скидки нет). См. https://developers.sber.ru/docs/ru/gigachat/api/tariffs.
COST_RATES: dict[str, tuple[float, float, float]] = {
    # --- Hydra → Cloud.ru / JOI proxy (approximate) ---
    # PRIMARY_LLM_MODEL=kimi-k2p6 — основная модель синтеза.
    "kimi-k2p6": (0.45, 0.45, 1.80),
    "kimi-k2p5": (0.30, 0.30, 1.20),
    "glm-5p1": (0.40, 0.40, 1.60),
    "glm-5": (0.30, 0.30, 1.20),
    "deepseek-v4-pro": (0.50, 0.10, 2.00),
    "deepseek-v3p2": (0.27, 0.07, 1.10),
    "deepseek-v3p1": (0.27, 0.07, 1.10),
    "minimax-m2p7": (0.35, 0.35, 1.40),
    "gpt-oss-120b": (0.20, 0.20, 0.80),
    # --- GigaChat 2 Max (Sber) B2B-тариф 2026-05 ---
    # Только Max — единственная модель GigaChat в стеке (см. .env.example
    # ROUTING_LLM_MODEL и `_DEFAULT_MODEL` в llm_disambiguate). Lite / Pro
    # сознательно не заведены: если кто-то случайно укажет их в env, лучше
    # пусть будет `cost=null` с debug-warning, чем тихий cost для модели,
    # которой не должно быть в нашем стеке.
    #
    # 9 750 ₽ / 15M tokens = 650 ₽/1M ≈ $7.065/1M.
    # Сбер: input == output, кеш-скидки нет.
    # Алиас "GigaChat-Max" — на случай если provider SDK резолвит имя без
    # префикса "2" (наследие от первого поколения в названиях env-vars).
    "GigaChat-2-Max": (650.0 / _RUB_USD_RATE,) * 3,
    "GigaChat-Max": (650.0 / _RUB_USD_RATE,) * 3,
}


def _strip_provider_prefix(model: str) -> str:
    """Снять префикс провайдера от usage_model в ouroboros.

    Ouroboros для openai-compatible моделей возвращает `resolved_model`
    в `_normalize_remote_response` как `target.usage_model`, а это
    `_qualified_model_name` — `"openai-compatible/kimi-k2p6"` (с префиксом).
    Наши COST_RATES ключи без префикса. Снимаем последнюю часть после
    последнего слэша; если слэша нет — возвращаем как есть.

    Также поддерживаем `::` разделитель (синтаксис self::model в ouroboros).
    """
    for sep in ("/", "::"):
        if sep in model:
            return model.rsplit(sep, 1)[-1].strip()
    return model.strip()


def compute_cost(
    model: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> Optional[float]:
    """Вернуть estimated cost в USD или None если ставка неизвестна.

    Args:
        model: имя модели. Может прийти с префиксом провайдера
            (`openai-compatible/kimi-k2p6`) или без (`kimi-k2p6`) — оба
            варианта пробуются.
        prompt_tokens: всего prompt-токенов (включая кешированные).
        completion_tokens: completion-токенов.
        cached_tokens: подмножество prompt_tokens, попавших в кеш (с дисконтом).

    Returns:
        cost_usd как float или None, если ставка не найдена ни в COST_RATES,
        ни в ouroboros.pricing. None означает «cost неизвестен», не «cost=0».
    """
    if not model:
        return None

    # 1. Наши ставки. Пробуем сначала full name (на случай если ставки заведены
    # с префиксом), потом stripped — частый случай для ouroboros usage_model.
    candidates = [model, _strip_provider_prefix(model)]
    for candidate in candidates:
        rates = COST_RATES.get(candidate)
        if rates is not None:
            input_rate, cached_rate, output_rate = rates
            non_cached = max(prompt_tokens - cached_tokens, 0)
            cost = (
                (non_cached * input_rate)
                + (cached_tokens * cached_rate)
                + (completion_tokens * output_rate)
            ) / 1_000_000.0
            return round(cost, 8)

    # 2. Fallback в ouroboros.pricing для OpenRouter-style.
    for candidate in candidates:
        try:
            from ouroboros.pricing import estimate_cost as _ouroboros_estimate

            cost = _ouroboros_estimate(
                candidate, prompt_tokens, completion_tokens, cached_tokens, 0
            )
            if cost:
                return round(float(cost), 8)
        except (ImportError, Exception) as exc:  # noqa: BLE001 — observability must not crash caller
            logger.debug(
                "ouroboros.pricing fallback failed for model=%r: %s", candidate, exc
            )

    # 3. Не знаем — честный None, не 0. Warning на debug-уровне для диагностики.
    logger.debug(
        "compute_cost: model=%r не найден в COST_RATES (tried: %s) "
        "и в ouroboros.pricing — возвращаем None",
        model,
        candidates,
    )
    return None


__all__ = ["compute_cost", "COST_RATES"]
