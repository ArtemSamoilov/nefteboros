"""Cost estimation для LLM-вызовов.

Иерархия (см. ADR-0024 §«Cost calculation»):

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


# (input_per_1m_usd, cached_per_1m_usd, output_per_1m_usd)
# Если cached_rate==input_rate — провайдер не даёт кеш-скидку.
COST_RATES: dict[str, tuple[float, float, float]] = {
    # Hydra → Cloud.ru / JOI proxy. См. https://hydragpt.ru/pricing.
    "kimi-k2p6": (0.45, 0.45, 1.80),
    "kimi-k2p5": (0.30, 0.30, 1.20),
    "glm-5p1": (0.40, 0.40, 1.60),
    "glm-5": (0.30, 0.30, 1.20),
    "deepseek-v4-pro": (0.50, 0.10, 2.00),
    "deepseek-v3p2": (0.27, 0.07, 1.10),
    "deepseek-v3p1": (0.27, 0.07, 1.10),
    "minimax-m2p7": (0.35, 0.35, 1.40),
    "gpt-oss-120b": (0.20, 0.20, 0.80),
    # GigaChat (Sber). Курс 92 ₽/$, тарифы B2B per 1M tokens.
    # https://developers.sber.ru/docs/ru/gigachat/api/tariffs
    "GigaChat": (0.22, 0.22, 0.65),
    "GigaChat-Pro": (0.65, 0.65, 1.96),
    "GigaChat-Max": (1.30, 1.30, 3.91),
    "GigaChat-Ultra": (2.61, 2.61, 7.83),
    "GigaChat-2-Max": (1.30, 1.30, 3.91),
}


def compute_cost(
    model: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> Optional[float]:
    """Вернуть estimated cost в USD или None если ставка неизвестна.

    Args:
        model: имя модели (resolved_model из ouroboros usage или из chat-объекта).
        prompt_tokens: всего prompt-токенов (включая кешированные).
        completion_tokens: completion-токенов.
        cached_tokens: подмножество prompt_tokens, попавших в кеш (с дисконтом).

    Returns:
        cost_usd как float или None, если ставка не найдена ни в COST_RATES,
        ни в ouroboros.pricing. None означает «cost неизвестен», не «cost=0».
    """
    if not model:
        return None

    # 1. Наши ставки (приоритет — знаем точно для нашего стека).
    rates = COST_RATES.get(model)
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
    try:
        from ouroboros.pricing import estimate_cost as _ouroboros_estimate

        cost = _ouroboros_estimate(model, prompt_tokens, completion_tokens, cached_tokens, 0)
        if cost:
            return round(float(cost), 8)
    except (ImportError, Exception) as exc:  # noqa: BLE001 — observability must not crash caller
        logger.debug("ouroboros.pricing fallback failed for model=%r: %s", model, exc)

    # 3. Не знаем — честный None, не 0.
    return None


__all__ = ["compute_cost", "COST_RATES"]
