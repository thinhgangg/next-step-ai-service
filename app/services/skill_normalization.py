from __future__ import annotations

import re
from typing import Iterable


_STOPWORDS = {
    "developer",
    "development",
    "engineer",
    "engineering",
    "programming",
    "software",
    "application",
    "apps",
    "framework",
    "tools",
    "tool",
    "specialization",
    "certificate",
    "certificates",
    "professional",
    "learn",
}


_NON_SKILL_ROLE_KEYS = {
    "backend",
    "back end",
    "back-end",
    "developer",
    "engineer",
    "frontend",
    "front end",
    "front-end",
    "full stack",
    "full-stack",
    "fullstack",
    "full stack developer",
    "full-stack developer",
    "fullstack developer",
    "mobile developer",
    "software developer",
    "software engineer",
    "technical leader",
    "tech lead",
}


_SKILL_ALIAS_GROUPS = {
    "agile scrum": ["agile", "scrum", "agile scrum", "agile and scrum"],
    "ai": ["ai", "artificial intelligence"],
    "aws": ["aws", "amazon web services"],
    "azure": ["azure", "microsoft azure"],
    "csharp": ["c#", "csharp", "c sharp", "dotnet c#"],
    "cpp": ["c++", "cpp", "c plus plus"],
    "ci cd": ["ci/cd", "cicd", "ci cd", "ci-cd", "continuous integration", "continuous delivery"],
    "css": ["css", "css3"],
    "devops": ["devops", "dev ops"],
    "django": ["django", "django framework"],
    "docker": ["docker", "container", "containers", "containerization"],
    "dotnet": [".net", "dotnet", "asp.net", "aspdotnet", "asp net", "net core", ".net core", "dotnet core"],
    "express": ["express", "express.js", "express js", "expressjs"],
    "fastapi": ["fastapi", "fast api"],
    "figma": ["figma"],
    "firebase": ["firebase", "google firebase"],
    "flask": ["flask", "flask framework"],
    "gcp": ["gcp", "google cloud", "google cloud platform"],
    "git": ["git", "version control"],
    "github": ["github", "git hub"],
    "gitlab": ["gitlab", "git lab"],
    "go": ["go", "golang"],
    "graphql": ["graphql", "graph ql"],
    "html": ["html", "html5"],
    "java": ["java", "core java"],
    "javascript": ["javascript", "js", "ecmascript", "es6", "es2015"],
    "jest": ["jest", "jestjs", "jest js"],
    "jquery": ["jquery", "j query"],
    "kubernetes": ["kubernetes", "k8s", "kube"],
    "laravel": ["laravel", "laravel framework"],
    "linux": ["linux", "gnu linux"],
    "machine learning": ["machine learning", "ml"],
    "mongodb": ["mongodb", "mongo", "mongo db"],
    "mysql": ["mysql", "mariadb", "maria db"],
    "nestjs": ["nestjs", "nest.js", "nest js", "nest"],
    "nextjs": ["nextjs", "next.js", "next js"],
    "nodejs": ["node.js", "nodejs", "node js", "node"],
    "nosql": ["nosql", "no sql"],
    "nuxtjs": ["nuxtjs", "nuxt.js", "nuxt js", "nuxt"],
    "oop": ["oop", "object oriented programming", "object-oriented programming"],
    "oracle": ["oracle", "oracle db", "oracle database"],
    "php": ["php"],
    "postgresql": ["postgresql", "postgres", "postgre", "postgres sql"],
    "postman": ["postman"],
    "python": ["python", "python3", "py"],
    "react": ["react", "react.js", "react js", "reactjs"],
    "react native": ["react native", "react-native", "rn"],
    "redis": ["redis", "redis cache"],
    "redux": ["redux", "redux toolkit", "rtk"],
    "rest api": ["rest api", "restful", "restful api", "rest", "api rest"],
    "sass": ["sass", "scss"],
    "spring boot": ["spring boot", "springboot"],
    "sql": ["sql", "structured query language"],
    "sql server": ["sql server", "mssql", "ms sql", "microsoft sql server"],
    "tailwind": ["tailwind", "tailwind css", "tailwindcss"],
    "typescript": ["typescript", "ts", "t s"],
    "ui ux": ["ui/ux", "ui ux", "ux ui", "user interface", "user experience"],
    "vuejs": ["vue", "vue.js", "vue js", "vuejs"],
}


_CANONICAL_SKILL_ALIASES: dict[str, str] = {}
for canonical_key, aliases in _SKILL_ALIAS_GROUPS.items():
    _CANONICAL_SKILL_ALIASES[canonical_key] = canonical_key
    for alias in aliases:
        _CANONICAL_SKILL_ALIASES[alias] = canonical_key


def normalize_skill_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("c plus plus", "c++")
    text = text.replace("c sharp", "c#")
    text = text.replace("node js", "node.js")
    text = text.replace("react js", "react.js")
    text = text.replace("restful api", "rest api")
    return text


def normalize_skill_key(value: str | None) -> str:
    text = normalize_skill_text(value)
    if not text:
        return ""

    text = text.replace("c#", "csharp")
    text = text.replace("c++", "cpp")
    text = text.replace("node.js", "nodejs")
    text = text.replace("react.js", "reactjs")
    text = text.replace(".net", "dotnet")
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonicalize_skill_key(value: str | None) -> str:
    key = normalize_skill_key(value)
    if not key:
        return ""
    return _CANONICAL_SKILL_ALIASES.get(key, key)


def is_non_skill_role_label(value: str | None) -> bool:
    key = normalize_skill_key(value)
    if not key:
        return True
    return key in {normalize_skill_key(item) for item in _NON_SKILL_ROLE_KEYS}


def get_skill_aliases(value: str | None) -> list[str]:
    canonical_key = canonicalize_skill_key(value)
    if not canonical_key:
        return []

    aliases = [canonical_key, *_SKILL_ALIAS_GROUPS.get(canonical_key, [])]
    result: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        key = normalize_skill_key(alias)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def tokenize_skill(value: str | None) -> set[str]:
    text = canonicalize_skill_key(value)
    if not text:
        return set()
    tokens = [item for item in re.split(r"[^a-zA-Z0-9+#]+", text) if item]
    return {item for item in tokens if item not in _STOPWORDS and len(item) >= 2}


def skill_similarity(left: str | None, right: str | None) -> float:
    a = tokenize_skill(left)
    b = tokenize_skill(right)
    if not a or not b:
        return 0.0
    union = a.union(b)
    if not union:
        return 0.0
    return len(a.intersection(b)) / len(union)


def build_skill_index(names: Iterable[str], aliases: Iterable[Iterable[str]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for name, alias_list in zip(names, aliases):
        canonical = canonicalize_skill_key(name)
        if not canonical:
            continue
        index[canonical] = name
        for alias in alias_list:
            alias_key = canonicalize_skill_key(alias)
            if alias_key and alias_key not in index:
                index[alias_key] = name
    return index
