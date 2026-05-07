from __future__ import annotations

import json
import os
import sys
from pathlib import Path

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.append(root_dir)

from app.db.session import get_standalone_db
from app.models.skill import Skill
from app.models.skill_course import SkillCourse
from app.services.skill_normalization import normalize_skill_key

GROUP_FILE = Path(root_dir) / "app" / "data" / "skill_relation_groups.json"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_group_file() -> None:
    _assert(GROUP_FILE.exists(), f"Missing group file: {GROUP_FILE}")
    payload = json.loads(GROUP_FILE.read_text(encoding="utf-8"))
    _assert("groups" in payload and isinstance(payload["groups"], list), "Group file missing groups")
    _assert(payload.get("meta", {}).get("total_skills", 0) > 0, "Group file has no skills")
    _assert(payload.get("meta", {}).get("total_edges", 0) > 0, "Group file has no edges")


def check_alias_matching() -> None:
    db = get_standalone_db()
    try:
        skills = db.query(Skill).all()
        _assert(len(skills) > 0, "No skills found in DB")

        samples = {
            "c++": "C++",
            "cpp": "C++",
            "node js": "Node.js",
            "rest api": "REST API",
            "react js": "React",
            "ts": "TypeScript",
            "k8s": "Kubernetes",
            "postgres": "PostgreSQL",
            "mariadb": "MySQL",
            "mongo": "MongoDB",
            "mssql": "MS SQL",
        }

        index = {}
        for skill in skills:
            if not skill.name:
                continue
            index[normalize_skill_key(skill.name)] = skill.name
            for alias in skill.aliases or []:
                index.setdefault(normalize_skill_key(alias), skill.name)

        for alias, expected in samples.items():
            matched = index.get(normalize_skill_key(alias))
            if matched != expected:
                raise AssertionError(f"Alias mapping failed: {alias} -> {matched}, expected {expected}")
    finally:
        db.close()


def check_duration_hours() -> None:
    db = get_standalone_db()
    try:
        rows = db.query(SkillCourse).all()
        _assert(len(rows) > 0, "No skill courses found in DB")
        _assert(any(row.duration_hours is not None for row in rows), "No duration_hours populated")
    finally:
        db.close()


def main() -> None:
    check_group_file()
    check_alias_matching()
    check_duration_hours()
    print("OK: skill pipeline validation passed")


if __name__ == "__main__":
    main()
