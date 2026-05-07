from __future__ import annotations

import os
import re
import sys
from collections import defaultdict

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.append(root_dir)

from app.db.session import get_standalone_db
from app.models.job_skill import JobSkill
from app.models.skill import Skill
from app.models.skill_course import SkillCourse
from app.services.course_duration_service import CourseDurationService
from app.services.skill_normalization import get_skill_aliases, normalize_skill_key


ALIASES_MAP: dict[str, list[str]] = {
    "C#": ["csharp", "c sharp", "dotnet c#"],
    ".NET": ["dotnet", "asp.net", "asp net", "net core", "dotnet core"],
    "Restful Api": ["rest api", "restful", "restful api"],
    "PostgreSQL": ["postgres", "postgresql", "postgre"],
    "MySQL": ["mysql", "mariadb"],
    "MongoDB": ["mongo", "mongodb"],
    "Redis": ["redis cache"],
    "JavaScript": ["js", "javascript"],
    "TypeScript": ["ts", "typescript"],
    "Node.js": ["nodejs", "node js", "node"],
    "React": ["reactjs", "react js", "react.js"],
    "Vue.js": ["vue", "vuejs", "vue js"],
    "Kubernetes": ["k8s", "kube"],
    "CI/CD": ["cicd", "ci cd", "continuous integration", "continuous delivery"],
    "AWS": ["amazon web services"],
    "GCP": ["google cloud", "google cloud platform"],
    "MS SQL": ["mssql", "sql server", "microsoft sql server"],
    "OOP": ["object oriented programming", "object-oriented programming"],
    "C/C++": ["c", "c plus plus", "cpp"],
    "C++": ["cpp", "c plus plus"],
}

COURSE_SEED: dict[str, list[dict[str, str]]] = {
    "Python": [
        {
            "platform": "Coursera",
            "title": "Python for Everybody",
            "url": "https://www.coursera.org/specializations/python",
            "duration": "8 weeks",
            "level": "beginner",
        },
        {
            "platform": "YouTube",
            "title": "Python Full Course",
            "url": "https://www.youtube.com/watch?v=rfscVS0vtbw",
            "duration": "12 hours",
            "level": "beginner",
        },
    ],
    "SQL": [
        {
            "platform": "Coursera",
            "title": "SQL for Data Science",
            "url": "https://www.coursera.org/learn/sql-for-data-science",
            "duration": "4 weeks",
            "level": "beginner",
        }
    ],
    "Docker": [
        {
            "platform": "Udemy",
            "title": "Docker & Kubernetes: The Practical Guide",
            "url": "https://www.udemy.com/course/docker-kubernetes-the-practical-guide/",
            "duration": "22 hours",
            "level": "intermediate",
        }
    ],
    "React": [
        {
            "platform": "Udemy",
            "title": "React - The Complete Guide",
            "url": "https://www.udemy.com/course/react-the-complete-guide-incl-redux/",
            "duration": "40 hours",
            "level": "intermediate",
        }
    ],
    ".NET": [
        {
            "platform": "Microsoft Learn",
            "title": ".NET learning path",
            "url": "https://learn.microsoft.com/en-us/training/dotnet/",
            "duration": "6 weeks",
            "level": "beginner",
        }
    ],
    "AWS": [
        {
            "platform": "Coursera",
            "title": "AWS Fundamentals",
            "url": "https://www.coursera.org/specializations/aws-fundamentals",
            "duration": "4 weeks",
            "level": "intermediate",
        }
    ],
}


def _normalize_aliases(raw_aliases: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in raw_aliases:
        alias = normalize_skill_key(value)
        if not alias or alias in seen:
            continue
        seen.add(alias)
        result.append(alias)
    return result


def seed_foundational_skills(db) -> int:
    existing = {
        normalize_skill_key(skill.name)
        for skill in db.query(Skill).all()
        if skill.name
    }

    inserted = 0
    for skill_name in ALIASES_MAP:
        key = normalize_skill_key(skill_name)
        if not key or key in existing:
            continue

        db.add(Skill(name=skill_name, category="technical", aliases=[], is_active=True))
        existing.add(key)
        inserted += 1

    db.commit()
    return inserted


def seed_aliases(db) -> int:
    updated = 0
    skills = db.query(Skill).all()
    for skill in skills:
        aliases = skill.aliases or []
        dynamic_aliases = [*ALIASES_MAP.get(skill.name, []), *get_skill_aliases(skill.name)]
        merged = _normalize_aliases([*aliases, *dynamic_aliases])

        if merged != aliases:
            skill.aliases = merged
            updated += 1

    db.commit()
    return updated


def tier_importance(db) -> int:
    updated = 0
    rows = db.query(JobSkill).all()

    for row in rows:
        current = float(row.importance or 0)
        evidence = (row.evidence_snippet or "").strip().lower() if hasattr(row, "evidence_snippet") else ""

        if current >= 2.5 and not evidence:
            tier = 2.0
        elif current >= 2.5:
            tier = 3.0
        elif current >= 1.5:
            tier = 2.0
        elif current > 1.0:
            tier = 1.0
        elif current == 1.0:
            tier = 2.0
        elif current >= 0.8:
            tier = 3.0
        elif current >= 0.55:
            tier = 2.0
        else:
            tier = 1.0

        if row.importance != tier:
            row.importance = tier
            updated += 1

    db.commit()
    return updated


def _parse_duration_hours(value: str | None) -> int | None:
    return CourseDurationService.parse_duration_hours(value)


def seed_courses(db) -> int:
    inserted = 0

    skill_by_name = {skill.name: skill for skill in db.query(Skill).all() if skill.name}
    existing_by_skill: dict[int, set[str]] = defaultdict(set)

    for row in db.query(SkillCourse).all():
        existing_by_skill[row.skill_id].add((row.title or "").strip().lower())

    for skill_name, courses in COURSE_SEED.items():
        skill = skill_by_name.get(skill_name)
        if not skill:
            continue

        for course in courses:
            title = (course["title"] or "").strip()
            if not title:
                continue
            title_key = title.lower()

            if title_key in existing_by_skill[skill.skill_id]:
                continue

            db.add(
                SkillCourse(
                    skill_id=skill.skill_id,
                    platform=course.get("platform"),
                    title=title,
                    url=course.get("url"),
                    duration=course.get("duration"),
                    duration_hours=_parse_duration_hours(course.get("duration")),
                    level=course.get("level"),
                )
            )
            existing_by_skill[skill.skill_id].add(title_key)
            inserted += 1

    db.commit()
    return inserted


def sync_course_hours(db) -> int:
    updated = 0
    rows = db.query(SkillCourse).all()

    for row in rows:
        parsed = _parse_duration_hours(row.duration)
        if parsed is None:
            continue
        if row.duration_hours != parsed:
            row.duration_hours = parsed
            updated += 1

    db.commit()
    return updated


def main() -> None:
    db = get_standalone_db()
    try:
        skills_inserted = seed_foundational_skills(db)
        aliases_updated = seed_aliases(db)
        importance_updated = tier_importance(db)
        courses_inserted = seed_courses(db)
        course_hours_updated = sync_course_hours(db)

        print(f"skills inserted: {skills_inserted}")
        print(f"aliases updated: {aliases_updated}")
        print(f"job_skills importance tiered: {importance_updated}")
        print(f"skill_courses inserted: {courses_inserted}")
        print(f"skill_courses duration_hours synced: {course_hours_updated}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
