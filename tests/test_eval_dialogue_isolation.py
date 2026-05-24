"""Regression guard для фикса изоляции диалогов E2E (ADR-0027, PR #78).

Защищает инвариант, на котором держится фикс tail-timeout:
1. `build_recent_sections` инжектит историю чата (`chat.jsonl`) в контекст
   задачи, и контекст растёт с числом записей — поэтому при общем чате
   диалоги контаминируют друг друга (root cause испорченного re-baseline
   v2.3.5: 43/100 в timeout от роста контекста до soft_cap).
2. Очистка `chat.jsonl` (что делает WSRunner через POST /api/chat/clear
   перед каждым диалогом) возвращает контекст к базовому размеру.
3. Eval-side: деривация HTTP-эндпоинта из ws-url + env-gate
   `EVAL_CHAT_ISOLATION`.

Если кто-то изменит сборку контекста так, что история перестанет
инжектиться/сбрасываться, — тест упадёт и заставит пересмотреть, нужна ли
ещё изоляция в eval. Чистый unit: без LLM, без сервера, без сети.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

from ouroboros.agent import Env
from ouroboros.context import build_llm_messages, build_recent_sections
from ouroboros.memory import Memory


def _setup(tmp_path: pathlib.Path) -> tuple[Env, Memory]:
    """Минимальный repo+drive в tmp_path → (Env, Memory)."""
    repo = tmp_path / "repo"
    (repo / "prompts").mkdir(parents=True, exist_ok=True)
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "prompts" / "SYSTEM.md").write_text("You are Ouroboros.", encoding="utf-8")
    (repo / "BIBLE.md").write_text("# Bible", encoding="utf-8")
    (repo / "docs" / "ARCHITECTURE.md").write_text("# Arch", encoding="utf-8")
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "state.json").write_text('{"spent_usd": 0}', encoding="utf-8")

    env = Env(repo_dir=repo, drive_root=tmp_path)
    memory = Memory(drive_root=tmp_path, repo_dir=repo)
    memory.ensure_files()
    return env, memory


def _write_chat(tmp_path: pathlib.Path, n: int) -> None:
    """n записей в logs/chat.jsonl (схема message_bus.log_chat)."""
    path = tmp_path / "logs" / "chat.jsonl"
    if n == 0:
        path.write_text("", encoding="utf-8")
        return
    lines = []
    for i in range(n):
        direction = "in" if i % 2 == 0 else "out"
        text = (
            "Какая spot-цена Brent и прогноз на полгода? "
            if direction == "in"
            else "Brent торгуется около $82/bbl; прогноз $84 [OPEC MOMR, p.14]. " * 6
        )
        lines.append(json.dumps(
            {"ts": "2026-05-24T20:00:00+00:00", "direction": direction,
             "chat_id": 1, "text": text},
            ensure_ascii=False,
        ))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ctx_tokens(env: Env, memory: Memory) -> int:
    _, cap = build_llm_messages(
        env=env, memory=memory,
        task={"id": "t", "type": "task", "chat_id": 1, "text": "Что с Brent?"},
        soft_cap_tokens=200_000,
    )
    return int(cap["estimated_tokens_before"])


# --- Механизм: история чата инжектится и растёт -----------------------------


def test_recent_chat_section_grows_with_history(tmp_path):
    env, memory = _setup(tmp_path)

    _write_chat(tmp_path, 0)
    empty = "\n".join(build_recent_sections(memory, env, task_id="t"))
    assert "Recent chat" not in empty

    _write_chat(tmp_path, 20)
    small = "\n".join(build_recent_sections(memory, env, task_id="t"))
    _write_chat(tmp_path, 100)
    large = "\n".join(build_recent_sections(memory, env, task_id="t"))

    assert "Recent chat" in small
    assert "Recent chat" in large
    # Больше истории → длиннее секция (монотонно).
    assert len(large) > len(small) > 0


def test_context_grows_with_history_and_resets_on_clear(tmp_path):
    env, memory = _setup(tmp_path)

    _write_chat(tmp_path, 0)
    base = _ctx_tokens(env, memory)

    _write_chat(tmp_path, 50)
    mid = _ctx_tokens(env, memory)

    _write_chat(tmp_path, 150)
    big = _ctx_tokens(env, memory)

    # Контекст растёт с историей — это и есть утечка при общем чате.
    assert base < mid < big
    # История материально раздувает контекст (не шум).
    assert big > base * 2

    # Очистка chat.jsonl (что делает /api/chat/clear) → назад к базе.
    _write_chat(tmp_path, 0)
    after_clear = _ctx_tokens(env, memory)
    assert after_clear < mid  # история ушла — контекст сброшен
    assert after_clear <= base  # без остаточного роста


# --- Eval-side: WSRunner ------------------------------------------------------


def test_http_base_from_ws():
    from scripts.eval.eval_e2e import WSRunner

    f = WSRunner._http_base_from_ws
    assert f("ws://localhost:8000/ws") == "http://localhost:8000"
    assert f("wss://host:9000/ws") == "https://host:9000"
    assert f("ws://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_isolation_gate_skips_clear_when_disabled(tmp_path, monkeypatch):
    """EVAL_CHAT_ISOLATION=0 → _clear_chat_history не делает HTTP-вызов."""
    import urllib.request

    from scripts.eval.eval_e2e import WSRunner

    calls: list[str] = []

    def _spy(req, *a, **kw):  # noqa: ANN001
        calls.append(getattr(req, "full_url", str(req)))
        raise AssertionError("urlopen must not be called when isolation disabled")

    monkeypatch.setattr(urllib.request, "urlopen", _spy)

    monkeypatch.setenv("EVAL_CHAT_ISOLATION", "0")
    runner = WSRunner(server_url="ws://localhost:8000/ws")
    asyncio.run(runner._clear_chat_history())  # must return early, no HTTP
    assert calls == []
