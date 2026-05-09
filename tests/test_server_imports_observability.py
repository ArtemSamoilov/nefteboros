"""Regression: server.py startup ДОЛЖЕН импортировать `nefteboros.observability`.

Без этого импорта Langfuse monkey-patches (apply_patches при загрузке
nefteboros.observability) НЕ применяются на Ouroboros agent loop, и
traces для chat-запросов без tool dispatch теряются.

Production-parity guard: тест воспроизводит startup server.py headless
(без uvicorn.run) и проверяет что observability модуль попал в
sys.modules. Если кто-то удалит eager import в server.py — тест упадёт.

См. ADR-0025, fix `eager-observability-import-server` (PR после v2.1.0).
"""
from __future__ import annotations

import pathlib
import sys

import pytest


def test_server_startup_imports_observability(tmp_path: pathlib.Path) -> None:
    """После загрузки server.py до main-блока nefteboros.observability
    должен быть в sys.modules → apply_patches() выполнен."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    server_py = repo_root / "server.py"
    assert server_py.is_file(), f"server.py не найден: {server_py}"

    # Загружаем server.py как module кодом до `if __name__ == "__main__"`,
    # чтобы избежать запуска uvicorn.run и блокировки теста.
    src = server_py.read_text(encoding="utf-8")
    cutoff = src.find('if __name__ == "__main__"')
    if cutoff == -1:
        cutoff = src.find("if __name__")
    assert cutoff > 0, "не нашли main-guard в server.py — формат изменился"

    # Чистим sys.modules — наш fixture не должен ловить ранее импортированный модуль.
    for mod_name in list(sys.modules):
        if mod_name == "nefteboros.observability" or mod_name.startswith(
            "nefteboros.observability."
        ):
            del sys.modules[mod_name]

    # Прогоняем headless. sys.path должен включать repo_root для импорта nefteboros.*.
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    fake_globals = {
        "__file__": str(server_py),
        "__name__": "__main__",
    }
    exec(  # noqa: S102 — controlled headless exec
        compile(src[:cutoff], str(server_py), "exec"),
        fake_globals,
    )

    assert "nefteboros.observability" in sys.modules, (
        "server.py startup НЕ импортирует nefteboros.observability — "
        "Langfuse patches не активируются, traces для chat без tool dispatch "
        "теряются. См. ADR-0025."
    )

    # Проверим что patches действительно применились (не только модуль загружен).
    from nefteboros.observability._ouroboros_patches import _PATCHED

    assert _PATCHED is True, (
        "nefteboros.observability импортирован, но _PATCHED=False — "
        "apply_patches() не отработал. Возможно LANGFUSE_ENABLED=false "
        "в окружении теста."
    )
