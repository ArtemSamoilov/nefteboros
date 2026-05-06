"""Smoke-тесты для prompts/SYSTEM.md после prepend Primary Mission блока.

См. ADR-0017. Проверяем, что:
- Файл читается без ошибок.
- Primary Mission блок присутствует и идёт ПЕРЕД оригинальным «I Am Ouroboros».
- Главные триггеры routing'а (`analyst_query`, ключевые asset'ы) упомянуты.
- Identity-секция Ouroboros сохранена (self-modify mechanics не сломаны).
- Размер файла не вырос катастрофически (raw cost-control).
"""

from __future__ import annotations

import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SYSTEM_MD = REPO_ROOT / "prompts" / "SYSTEM.md"


def _read_system_md() -> str:
    return SYSTEM_MD.read_text(encoding="utf-8")


def test_system_md_exists_and_readable() -> None:
    assert SYSTEM_MD.is_file(), f"{SYSTEM_MD} not found"
    text = _read_system_md()
    assert len(text) > 1000, "SYSTEM.md подозрительно короткий"


def test_primary_mission_block_present_at_top() -> None:
    """Primary Mission блок должен быть первым H1 в файле."""
    text = _read_system_md()
    lines = text.splitlines()
    first_h1 = next((ln for ln in lines if ln.startswith("# ")), "")
    assert "Primary Mission" in first_h1, (
        f"первый H1 должен быть 'Primary Mission', got: {first_h1!r}"
    )
    assert "Старший аналитик" in first_h1
    assert "Sber" in first_h1 or "Сбер" in first_h1.lower()


def test_ouroboros_identity_preserved_after_primary_mission() -> None:
    """«I Am Ouroboros» секция всё ещё в файле, после Primary Mission."""
    text = _read_system_md()
    pm_idx = text.find("# Primary Mission")
    ouro_idx = text.find("# I Am Ouroboros")
    assert pm_idx >= 0, "Primary Mission секция отсутствует"
    assert ouro_idx >= 0, "I Am Ouroboros секция отсутствует"
    assert pm_idx < ouro_idx, (
        "Primary Mission должна идти ПЕРЕД I Am Ouroboros"
    )


def test_analyst_query_tool_mentioned() -> None:
    """Promтт явно упоминает analyst_query tool."""
    text = _read_system_md()
    assert "analyst_query" in text, (
        "промпт не упоминает analyst_query tool"
    )


def test_key_assets_mentioned() -> None:
    """Промпт упоминает ключевые активы (триггеры для агента)."""
    text = _read_system_md()
    for asset in ("Brent", "WTI", "Urals", "TTF", "Henry Hub"):
        assert asset in text, f"актив {asset!r} не упомянут в промпте"


def test_ru_context_keywords_mentioned() -> None:
    """Триггеры РФ-контекста явно объявлены."""
    text = _read_system_md()
    for kw in ("Минфин", "бюджет", "нефтегаздоход"):
        assert kw in text, f"РФ-keyword {kw!r} не упомянут"


def test_when_not_to_call_section_present() -> None:
    """Промпт явно объявляет, на какие запросы НЕ вызывать analyst_query."""
    text = _read_system_md()
    assert "Когда НЕ вызывать" in text or "не вызывать" in text.lower()


def test_response_format_section_present() -> None:
    """Промпт инструктирует, как форматировать ответ после tool-call'а."""
    text = _read_system_md()
    assert "synthesis" in text
    assert "citations" in text


def test_known_limitations_section_present() -> None:
    """Промпт явно перечисляет известные ограничения analyst pipeline."""
    text = _read_system_md()
    assert (
        "RAG/web overlay" in text
        or "RAG / web overlay" in text
    ), "не упомянут pending RAG/web overlay"
    assert "shock" in text.lower() or "Iran" in text


def test_size_within_reasonable_bounds() -> None:
    """Файл не должен быть raздут — sanity check на size."""
    text = _read_system_md()
    n_chars = len(text)
    n_lines = len(text.splitlines())
    # Original ~47K, prepend ~5K → ~52K. Bound с запасом.
    assert n_chars < 60_000, (
        f"SYSTEM.md слишком большой: {n_chars} chars (ожидаем <60K)"
    )
    # Original ~880 lines, prepend ~95 → max ~1000
    assert n_lines < 1100, (
        f"SYSTEM.md слишком длинный: {n_lines} строк (ожидаем <1100)"
    )
