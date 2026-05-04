"""Smoke-тесты роутера и фабрик LLM-провайдеров.

Сетевые вызовы НЕ делаются. Тесты проверяют только:
  - валидацию env (отсутствие credentials → понятный ValueError)
  - резолвинг провайдера/модели через env vs per-call override
  - конструирование объекта без исключения

Реальные интеграционные тесты (с вызовом LLM) — в `scripts/eval/eval_llm.py`,
помечены `@pytest.mark.integration`.
"""

from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Router resolution
# ---------------------------------------------------------------------------


def test_unknown_provider_raises(monkeypatch):
    from nefteboros.llm.router import get_chat_model
    monkeypatch.delenv("PRIMARY_LLM_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_chat_model(provider="openai")


def test_hydragpt_alias_normalizes_to_hydra(monkeypatch):
    """provider='hydragpt' must alias to 'hydra'."""
    from nefteboros.llm.router import _normalize_provider
    assert _normalize_provider("hydragpt") == "hydra"
    assert _normalize_provider("HYDRA") == "hydra"
    assert _normalize_provider("GigaChat") == "gigachat"


# ---------------------------------------------------------------------------
# HydraGPT
# ---------------------------------------------------------------------------


def test_hydra_missing_api_key(monkeypatch):
    from nefteboros.llm.hydra import get_hydra_chat_model
    monkeypatch.delenv("HYDRA_API_KEY", raising=False)
    with pytest.raises(ValueError, match="HYDRA_API_KEY"):
        get_hydra_chat_model()


def test_hydra_factory_with_explicit_args(monkeypatch):
    """Фабрика должна построить ChatOpenAI с правильным base_url и моделью."""
    monkeypatch.delenv("HYDRA_API_KEY", raising=False)
    from nefteboros.llm.hydra import get_hydra_chat_model, HYDRA_DEFAULT_BASE_URL

    llm = get_hydra_chat_model(
        api_key="hydra_test_key",
        model="kimi-k2p6",
    )
    # ChatOpenAI хранит base_url через openai_api_base или _base_url — пинить
    # точное имя поля рискованно, а вот строковое представление точно содержит base.
    repr_ = repr(llm)
    assert "kimi-k2p6" in repr_ or llm.model_name == "kimi-k2p6"
    # base_url должен быть либо явно задан, либо HYDRA_DEFAULT_BASE_URL
    base = getattr(llm, "openai_api_base", None) or getattr(llm, "_base_url", None) or str(getattr(llm, "client", ""))
    assert HYDRA_DEFAULT_BASE_URL in str(base) or "hydragpt.ru" in str(base)


def test_hydra_reads_env(monkeypatch):
    monkeypatch.setenv("HYDRA_API_KEY", "hydra_env_key")
    monkeypatch.setenv("HYDRA_DEFAULT_MODEL", "glm-5p1")
    from nefteboros.llm.hydra import get_hydra_chat_model

    llm = get_hydra_chat_model()
    # model должен подтянуться из HYDRA_DEFAULT_MODEL
    assert llm.model_name == "glm-5p1" or "glm-5p1" in repr(llm)


# ---------------------------------------------------------------------------
# GigaChat
# ---------------------------------------------------------------------------


def test_gigachat_missing_credentials(monkeypatch):
    from nefteboros.llm.gigachat import get_gigachat_chat_model
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    with pytest.raises(ValueError, match="GIGACHAT_CREDENTIALS"):
        get_gigachat_chat_model()


def test_gigachat_factory_constructs(monkeypatch):
    """Фабрика должна построить GigaChat без исключения при наличии creds."""
    pytest.importorskip("langchain_gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "fake_b64_creds")
    from nefteboros.llm.gigachat import get_gigachat_chat_model

    llm = get_gigachat_chat_model(model="GigaChat-Max")
    assert llm is not None
    # GigaChat хранит модель в self.model
    assert getattr(llm, "model", None) == "GigaChat-Max" or "GigaChat-Max" in repr(llm)


def test_gigachat_scope_default(monkeypatch):
    pytest.importorskip("langchain_gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "fake")
    monkeypatch.delenv("GIGACHAT_SCOPE", raising=False)
    from nefteboros.llm.gigachat import get_gigachat_chat_model
    llm = get_gigachat_chat_model()
    # scope должен быть GIGACHAT_API_PERS по умолчанию
    assert getattr(llm, "scope", None) == "GIGACHAT_API_PERS" or "GIGACHAT_API_PERS" in repr(llm)
