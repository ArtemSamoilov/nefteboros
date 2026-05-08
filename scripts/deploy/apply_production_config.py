#!/usr/bin/env python3
"""Apply nefteboros production config to settings.json + .env.

Идемпотентный patch для production-сервера. Сохраняет существующие
secrets (OUROBOROS_NETWORK_PASSWORD, HYDRA_API_KEY, GIGACHAT_*),
обновляет только то, что отвечает за выбор моделей и фичи Ouroboros.

Контекст (PR #27 / fix prod config):
- Главный bug: Ouroboros main loop default `OUROBOROS_MODEL` падал на
  `anthropic/claude-opus-4.7` без `ANTHROPIC_API_KEY` → fallback chain
  «All models down». Settings.json содержал только NETWORK_PASSWORD,
  все default'ы из `config.py:SETTINGS_DEFAULTS` уезжали в Anthropic.
- Желаемое разделение: PRIMARY = Kimi-k2p6 через Hydra
  (для main loop + analyst graph synthesize), ROUTING = GigaChat для
  лёгких задач (intent classify, llm_disambiguate в analyst graph).

Запуск на сервере:

    HYDRA_API_KEY=hydra_xxx ./scripts/deploy/apply_production_config.py

Скрипт:
1. Читает существующий settings.json; preserves NETWORK_PASSWORD и любые
   ключи которые скрипт явно не обновляет.
2. Patches Ouroboros model keys + skill auto-enable + review models.
3. Patches .env PRIMARY/ROUTING LLM toggles. Не трогает Hydra/GigaChat
   credentials, EIA_API_KEY, и пр.
4. Делает `.bak` копии перед изменениями.

После запуска: `systemctl restart nefteboros`.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
from datetime import datetime, timezone


# Production paths on Timeweb server (см. docs/deploy/production-config.md)
SETTINGS_PATH = pathlib.Path(
    os.environ.get("OUROBOROS_SETTINGS_PATH",
                   "/root/nefteboros/data/ouroboros/settings.json")
)
ENV_PATH = pathlib.Path(os.environ.get("NEFTEBOROS_ENV_PATH", "/root/nefteboros/.env"))


# Желаемое состояние settings.json — только модельные ключи + auto-enable
# + review models. Network password и любые user-set keys preserved.
DESIRED_OUROBOROS_SETTINGS = {
    # Main loop + tool calls + light + fallback — все на Kimi (без Anthropic
    # fallback потому что ANTHROPIC_API_KEY не настроен в production).
    "OUROBOROS_MODEL": "openai-compatible::kimi-k2p6",
    "OUROBOROS_MODEL_CODE": "openai-compatible::kimi-k2p6",
    "OUROBOROS_MODEL_LIGHT": "openai-compatible::kimi-k2p6",
    "OUROBOROS_MODEL_FALLBACK": "openai-compatible::kimi-k2p6",
    # Hydra OpenAI-compatible endpoint — base URL + key. Key берётся из
    # HYDRA_API_KEY env var при запуске скрипта.
    "OPENAI_COMPATIBLE_BASE_URL": "https://hydragpt.ru/v1",
    # Skill auto-enable — оба tool'а сразу видны агенту в первом round'е.
    "OUROBOROS_AUTO_ENABLE_SKILLS": (
        "neftegaz_analyst:analyst_query,neftegaz_analyst:rag_search"
    ),
    # Phase 4 review pipeline — non-reasoning Hydra-моделями (см. ADR-0019
    # § «Reviewer models»: Kimi/GLM CoT-стиль не подходит как reviewer'ы).
    "OUROBOROS_REVIEW_MODELS": (
        "openai-compatible::deepseek-v4-pro,"
        "openai-compatible::minimax-m2p7,"
        "openai-compatible::gpt-oss-120b"
    ),
    "OUROBOROS_SCOPE_REVIEW_MODEL": "openai-compatible::deepseek-v4-pro",
    "OUROBOROS_REVIEW_ENFORCEMENT": "advisory",
    # Runtime mode — advanced (см. config.py SETTINGS_DEFAULTS).
    "OUROBOROS_RUNTIME_MODE": "advanced",
    # Output cap — 256K, см. ADR-0021.
    "OUROBOROS_MAX_OUTPUT_TOKENS": 256_000,
}


# .env keys которые скрипт переписывает. Остальные строки .env preserved as-is.
DESIRED_ENV_OVERRIDES = {
    # PRIMARY = Kimi-k2p6 (для analyst graph synthesize). Раньше было
    # gigachat / GigaChat-2-Max — инверсировано по архитектурному решению
    # «PRIMARY = Кими, GigaChat для лёгких задач».
    "PRIMARY_LLM_PROVIDER": "hydra",
    "PRIMARY_LLM_MODEL": "kimi-k2p6",
    # ROUTING — light, для intent classify / llm_disambiguate. GigaChat-Max
    # справляется + дешевле чем Kimi на этих коротких задачах.
    "ROUTING_LLM_PROVIDER": "gigachat",
    "ROUTING_LLM_MODEL": "GigaChat-2-Max",
    # Output cap для всех LLM-вызовов (см. ADR-0021).
    "OUROBOROS_MAX_OUTPUT_TOKENS": "256000",
    # Обязательный server-host для public bind (PR #18-26).
    "OUROBOROS_SERVER_HOST": "0.0.0.0",
}


def _backup(path: pathlib.Path) -> pathlib.Path | None:
    if not path.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, backup)
    return backup


def patch_settings(hydra_api_key: str) -> dict:
    """Idempotent patch settings.json с сохранением existing keys."""
    settings: dict = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"  WARN: settings.json corrupt — starting fresh ({exc})", file=sys.stderr)
            settings = {}

    backup = _backup(SETTINGS_PATH)
    if backup:
        print(f"  Backup: {backup}")

    # Apply desired keys (override values, preserve everything else).
    settings.update(DESIRED_OUROBOROS_SETTINGS)

    # API key from env — не записываем если уже задан вручную (preserves
    # rotated keys).
    if hydra_api_key and not settings.get("OPENAI_COMPATIBLE_API_KEY"):
        settings["OPENAI_COMPATIBLE_API_KEY"] = hydra_api_key

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
    print(f"  settings.json updated ({len(settings)} keys)")
    return settings


def patch_env() -> None:
    """Idempotent patch .env — переписывает только desired keys."""
    if not ENV_PATH.exists():
        print(f"  WARN: {ENV_PATH} not found, skipping .env patch", file=sys.stderr)
        return

    backup = _backup(ENV_PATH)
    if backup:
        print(f"  Backup: {backup}")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in DESIRED_ENV_OVERRIDES:
            out_lines.append(f"{key}={DESIRED_ENV_OVERRIDES[key]}")
            seen.add(key)
        else:
            out_lines.append(line)

    # Append keys that weren't present.
    missing = [k for k in DESIRED_ENV_OVERRIDES if k not in seen]
    if missing:
        out_lines.append("")
        out_lines.append(
            f"# === added by apply_production_config.py {datetime.now(timezone.utc).isoformat()} ==="
        )
        for key in missing:
            out_lines.append(f"{key}={DESIRED_ENV_OVERRIDES[key]}")

    ENV_PATH.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"  .env updated (overrode {len(seen)}, appended {len(missing)})")


def main() -> int:
    print(f"=== Applying production config @ {datetime.now(timezone.utc).isoformat()} ===")
    print(f"  Settings: {SETTINGS_PATH}")
    print(f"  Env:      {ENV_PATH}")
    hydra_key = os.environ.get("HYDRA_API_KEY", "").strip()
    if not hydra_key:
        print(
            "  WARN: HYDRA_API_KEY env var empty — settings.json "
            "OPENAI_COMPATIBLE_API_KEY won't be touched (existing value preserved)",
            file=sys.stderr,
        )
    print()

    print("[1/2] settings.json")
    patch_settings(hydra_key)
    print()

    print("[2/2] .env")
    patch_env()
    print()

    print("Done. Restart service:")
    print("  systemctl restart nefteboros")
    return 0


if __name__ == "__main__":
    sys.exit(main())
