from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set
import re

from app.schemas.analyzer import JobMatchRequest, JobMatchResponse, ScoreBreakdown
from app.services.skill_normalization import canonicalize_skill_key, is_non_skill_role_label


LEVEL_MAP: Dict[str, int] = {
    "intern": 0,
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
}


@dataclass
class MatchComponents:
    keyword: float
    skill: float
    title: float
    ats: float


class JobMatchingService:
    @staticmethod
    def _normalize_importance(raw_importance: float) -> float:
        value = float(raw_importance or 0)
        if value <= 0:
            return 0.0

        if value in {1.0, 2.0, 3.0}:
            return min(value / 3.0, 1.0)

        if value <= 1.0:
            return value

        return min(value / 3.0, 1.0)

    @staticmethod
    def _normalize_skill_name(value: str) -> str:
        return canonicalize_skill_key(value)

    @staticmethod
    def _tokenize(value: str) -> Set[str]:
        return {token for token in re.split(r"[^a-zA-Z0-9+#]+", (value or "").lower()) if token}

    @staticmethod
    def _jaccard(left: Set[str], right: Set[str]) -> float:
        if not left or not right:
            return 0.0
        union = left.union(right)
        if not union:
            return 0.0
        return len(left.intersection(right)) / len(union)


    @staticmethod
    def calculate_keyword_match(payload: JobMatchRequest) -> float:
        if not payload.job_skills:
            return 0.0

        cv_skill_keys = [
            JobMatchingService._normalize_skill_name(skill.name)
            for skill in payload.cv_skills
            if skill.name and not is_non_skill_role_label(skill.name)
        ]

        if not cv_skill_keys:
            return 0.0

        weighted_sum = 0.0
        weight_total = 0.0

        for required in payload.job_skills:
            if is_non_skill_role_label(required.name):
                continue
            skill_key = JobMatchingService._normalize_skill_name(required.name)
            weight = JobMatchingService._normalize_importance(required.importance)

            match_value = 0.0
            if skill_key in cv_skill_keys:
                match_value = 1.0
            else:
                required_tokens = JobMatchingService._tokenize(skill_key)
                for cv_key in cv_skill_keys:
                    if not cv_key:
                        continue
                    cv_tokens = JobMatchingService._tokenize(cv_key)
                    overlap = JobMatchingService._jaccard(required_tokens, cv_tokens)
                    if overlap >= 0.3:
                        match_value = 0.5
                        break
                    if skill_key in cv_key or cv_key in skill_key:
                        match_value = 0.5
                        break

            weighted_sum += weight * match_value
            weight_total += weight

        if weight_total == 0:
            return 0.0

        return weighted_sum / weight_total

    @staticmethod
    def calculate_skill_match(payload: JobMatchRequest) -> float:
        if not payload.job_skills:
            return 0.0

        cv_items = [skill for skill in payload.cv_skills if skill.name and not is_non_skill_role_label(skill.name)]
        if not cv_items:
            return 0.0

        total = 0.0
        job_skill_count = 0
        for required in payload.job_skills:
            if is_non_skill_role_label(required.name):
                continue
            job_skill_count += 1
            req_name = JobMatchingService._normalize_skill_name(required.name)
            required_tokens = JobMatchingService._tokenize(req_name)

            best = 0.0
            for cv_skill in cv_items:
                cv_name = JobMatchingService._normalize_skill_name(cv_skill.name)
                if cv_name == req_name:
                    score = 1.0
                else:
                    overlap = JobMatchingService._jaccard(required_tokens, JobMatchingService._tokenize(cv_name))
                    score = min(0.6, overlap)

                score *= cv_skill.proficiency
                if score > best:
                    best = score

            total += best

        if job_skill_count == 0:
            return 0.0

        return total / job_skill_count

    @staticmethod
    def calculate_title_match(payload: JobMatchRequest) -> float:
        cv_title = (payload.cv_title or "").strip()
        job_title = (payload.job_title or "").strip()

        if cv_title and job_title:
            left = JobMatchingService._tokenize(cv_title)
            right = JobMatchingService._tokenize(job_title)
            token_score = JobMatchingService._jaccard(left, right)
            if cv_title.lower() == job_title.lower():
                token_score = 1.0
            return min(1.0, token_score)

        cv_level = LEVEL_MAP.get((payload.cv_level or "").strip().lower(), 1)
        job_level = LEVEL_MAP.get((payload.job_level or "").strip().lower(), 1)
        diff = abs(cv_level - job_level)
        return max(0.0, 1.0 - 0.3 * diff)

    @staticmethod
    def calculate_ats_readability(payload: JobMatchRequest) -> float:
        if payload.ats_parse_score is not None:
            return max(0.0, min(1.0, payload.ats_parse_score))

        score = 0.0
        if payload.cv_skills:
            score += 0.5
        if payload.cv_years_experience > 0:
            score += 0.2
        if (payload.cv_level or "").strip().lower() in LEVEL_MAP:
            score += 0.2
        if payload.preferred_locations:
            score += 0.1
        return min(1.0, score)

    @staticmethod
    def _matched_missing_skills(payload: JobMatchRequest) -> tuple[List[str], List[str]]:
        cv_skill_keys: Set[str] = {
            JobMatchingService._normalize_skill_name(skill.name)
            for skill in payload.cv_skills
            if JobMatchingService._normalize_skill_name(skill.name) and not is_non_skill_role_label(skill.name)
        }

        matched: list[str] = []
        missing: list[str] = []
        seen: set[str] = set()
        for skill in payload.job_skills:
            if is_non_skill_role_label(skill.name):
                continue
            key = JobMatchingService._normalize_skill_name(skill.name)
            if not key or key in seen:
                continue
            seen.add(key)
            if key in cv_skill_keys:
                matched.append(skill.name)
            else:
                missing.append(skill.name)

        matched.sort(key=str.lower)
        missing.sort(key=str.lower)
        return matched, missing

    @staticmethod
    def calculate_job_match(payload: JobMatchRequest) -> JobMatchResponse:
        components = MatchComponents(
            keyword=JobMatchingService.calculate_keyword_match(payload),
            skill=JobMatchingService.calculate_skill_match(payload),
            title=JobMatchingService.calculate_title_match(payload),
            ats=JobMatchingService.calculate_ats_readability(payload),
        )

        score = (
            0.60 * components.keyword
            + 0.20 * components.skill
            + 0.10 * components.title
            + 0.10 * components.ats
        )

        matched_skills, missing_skills = JobMatchingService._matched_missing_skills(payload)

        return JobMatchResponse(
            score=round(score * 100),
            scoreBreakdownJson=ScoreBreakdown(
                skillMatch=round(components.skill * 100),
                experienceMatch=0,
                levelMatch=round(components.title * 100),
                salaryMatch=0,
                locationMatch=round(components.ats * 100),
                keywordMatch=round(components.keyword * 100),
                titleMatch=round(components.title * 100),
                atsReadability=round(components.ats * 100),
            ),
            missingSkills=missing_skills,
            matchedSkills=matched_skills,
        )
