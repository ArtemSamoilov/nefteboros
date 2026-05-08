"""Unit-тесты для nefteboros.observability.cost.

Главное: проверка нормализации имени модели — ouroboros возвращает
`usage["resolved_model"] = "openai-compatible/kimi-k2p6"` (с префиксом),
наши COST_RATES ключи без префикса. compute_cost должен снимать префикс.

См. ADR-0024 §«Cost calculation» и багфикс PR #37.
"""

from __future__ import annotations

import pytest

from nefteboros.observability.cost import (
    COST_RATES,
    _strip_provider_prefix,
    compute_cost,
)


class TestStripProviderPrefix:
    """Снятие префикса провайдера от имени модели."""

    def test_openai_compatible_slash(self):
        assert _strip_provider_prefix("openai-compatible/kimi-k2p6") == "kimi-k2p6"

    def test_openai_slash(self):
        assert _strip_provider_prefix("openai/gpt-4") == "gpt-4"

    def test_anthropic_slash(self):
        assert (
            _strip_provider_prefix("anthropic/claude-sonnet-4.6") == "claude-sonnet-4.6"
        )

    def test_double_colon(self):
        assert _strip_provider_prefix("openai-compatible::kimi-k2p6") == "kimi-k2p6"

    def test_no_prefix(self):
        assert _strip_provider_prefix("kimi-k2p6") == "kimi-k2p6"

    def test_no_prefix_gigachat(self):
        assert _strip_provider_prefix("GigaChat-Max") == "GigaChat-Max"

    def test_whitespace(self):
        assert _strip_provider_prefix("  openai/gpt-4  ") == "gpt-4"


class TestComputeCost:
    """compute_cost: иерархия наши rates → ouroboros.pricing → None."""

    def test_kimi_k2p6_with_provider_prefix(self):
        """Регрессионный тест на bug замеченный 2026-05-08:
        ouroboros usage_model приходит как 'openai-compatible/kimi-k2p6',
        compute_cost должен снимать префикс и находить в COST_RATES."""
        cost = compute_cost("openai-compatible/kimi-k2p6", 1000, 500)
        assert cost is not None, (
            "compute_cost вернул None для prefixed name — bug в _strip_provider_prefix?"
        )
        assert cost > 0

    def test_kimi_k2p6_without_prefix(self):
        cost = compute_cost("kimi-k2p6", 1000, 500)
        assert cost is not None
        assert cost > 0

    def test_kimi_k2p6_prefix_equals_no_prefix(self):
        """Главное свойство: префикс не должен менять итоговый cost."""
        with_prefix = compute_cost("openai-compatible/kimi-k2p6", 1000, 500)
        without_prefix = compute_cost("kimi-k2p6", 1000, 500)
        assert with_prefix == without_prefix

    def test_kimi_k2p6_value(self):
        """Sanity: 1000 input + 500 output @ (0.45, _, 1.80) per 1M.
        Expected: 1000*0.45e-6 + 500*1.80e-6 = 0.00045 + 0.0009 = 0.00135."""
        cost = compute_cost("kimi-k2p6", 1000, 500)
        assert cost == pytest.approx(0.00135, rel=1e-3)

    def test_gigachat_2_max(self):
        """GigaChat 2 Max: 650 ₽/1M @ курс 92 ₽/$ ≈ 7.065 $/1M.
        Сбер берёт input==output (cached==input, скидки нет).
        1000 prompt + 500 completion → (1000+500) * 7.065e-6 ≈ 0.0106."""
        cost = compute_cost("GigaChat-2-Max", 1000, 500)
        assert cost is not None
        assert cost == pytest.approx(1500 * 650.0 / 92.0 / 1_000_000.0, rel=1e-3)

    def test_gigachat_max_alias(self):
        """Алиас GigaChat-Max == GigaChat-2-Max (на случай если SDK
        резолвит имя без префикса '2')."""
        c1 = compute_cost("GigaChat-Max", 1000, 500)
        c2 = compute_cost("GigaChat-2-Max", 1000, 500)
        assert c1 == c2

    def test_gigachat_lite_pro_returns_none(self):
        """Lite и Pro сознательно не в COST_RATES (см. cost.py): в стеке
        только Max. Если кто-то указал Lite/Pro в env — `cost=null` с
        debug-warning, а не молчаливый cost для не-нашей модели."""
        assert compute_cost("GigaChat-2-Lite", 1000, 500) is None
        assert compute_cost("GigaChat-2-Pro", 1000, 500) is None

    def test_gigachat_input_equals_output(self):
        """Свойство Сбера: input и output по одинаковой цене."""
        # 1000 input only vs 0 input + 1000 output — должны давать тот же cost.
        c_input_only = compute_cost("GigaChat-2-Max", 1000, 0)
        c_output_only = compute_cost("GigaChat-2-Max", 0, 1000)
        assert c_input_only == c_output_only

    def test_unknown_model_returns_none(self):
        """Для неизвестной модели — None, не 0. Семантически разные кейсы."""
        cost = compute_cost("nonexistent-model-xyz-2099", 1000, 500)
        assert cost is None

    def test_empty_model_returns_none(self):
        assert compute_cost(None, 1000, 500) is None
        assert compute_cost("", 1000, 500) is None

    def test_zero_tokens(self):
        cost = compute_cost("kimi-k2p6", 0, 0)
        assert cost == 0.0

    def test_cached_tokens_discount(self):
        """Если cached < input — non-cached платится полной ставкой,
        cached — кешированной (для Hydra cached==input → разницы нет,
        для DeepSeek — разница есть)."""
        # DeepSeek-v4-Pro: input=0.50, cached=0.10, output=2.00
        # 1000 prompt где 800 кешированных + 200 output
        cost = compute_cost("deepseek-v4-pro", 1000, 200, cached_tokens=800)
        # non-cached 200 * 0.50 + cached 800 * 0.10 + output 200 * 2.00
        # = 100 + 80 + 400 = 580 (e-6) = 0.00058
        assert cost == pytest.approx(0.00058, rel=1e-3)


class TestCostRatesCompleteness:
    """Sanity-чек что наш стек покрыт COST_RATES."""

    def test_primary_models_covered(self):
        """Модели из .env.example PRIMARY/ROUTING должны быть в COST_RATES."""
        # См. .env.example: PRIMARY_LLM_MODEL=kimi-k2p6, ROUTING_LLM_MODEL=GigaChat-Max
        assert "kimi-k2p6" in COST_RATES
        assert "GigaChat-Max" in COST_RATES
