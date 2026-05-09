"""Включить skill (extension) в drive_root: write enabled.json + review.json.

Без этого `is_extension_live` возвращает False → tool dispatch handlers
не доступны агенту, и WS chat запросы не вызывают web_search/rag_search/
analyst_query.

Использование (внутри контейнера):
    docker exec nefteboros-web python scripts/enable_skill_in_drive.py neftegaz_analyst

После — рестарт server'а контейнера чтобы он подхватил state:
    docker compose -f deploy/docker-compose.yml restart web
"""

from __future__ import annotations

import os
import pathlib
import sys


def main() -> int:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

    name = sys.argv[1] if len(sys.argv) > 1 else "neftegaz_analyst"
    drive_root = pathlib.Path(
        os.environ.get("OUROBOROS_DATA_DIR", "/app/data")
    )

    from ouroboros.skill_loader import (
        SkillReviewState,
        find_skill,
        save_enabled,
        save_review_state,
    )

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    print(f"[enable] drive_root={drive_root}")
    print(f"[enable] repo_root={repo_root}")
    print(f"[enable] skill={name}")

    skill = find_skill(drive_root, name, repo_path=str(repo_root))
    if skill is None:
        print(f"[enable] FAIL: skill {name!r} not found in {repo_root}/skills")
        return 1

    save_enabled(drive_root, name, True)
    save_review_state(
        drive_root,
        name,
        SkillReviewState(status="pass", content_hash=skill.content_hash),
    )
    print(f"[enable] enabled.json + review.json written for {name}")
    print(f"[enable] content_hash={skill.content_hash[:16]}…")
    print(f"[enable] state_dir contents:")
    state_dir = drive_root / "state" / "skills" / name
    for f in sorted(state_dir.iterdir()):
        print(f"  {f.name}: {f.stat().st_size}b")
    return 0


if __name__ == "__main__":
    sys.exit(main())
