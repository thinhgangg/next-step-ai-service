from __future__ import annotations

from typing import Dict, List

from app.schemas.analyzer import (
    CertificationGap,
    ExperienceGap,
    GapAnalysisRequest,
    GapAnalysisResponse,
    LevelGap,
    MissingSkillGap,
    SkillGap,
    WeakSkillGap,
)
from app.services.job_matching_service import LEVEL_MAP
from app.services.skill_normalization import canonicalize_skill_key, expand_skill_labels, is_non_skill_role_label


class AnalysisService:
    @staticmethod
    def normalize_importance(raw_importance: float) -> float:
        value = float(raw_importance or 0)
        if value <= 0:
            return 0.0

        if value in {1.0, 2.0, 3.0}:
            return min(value / 3.0, 1.0)

        if value <= 1.0:
            return value

        return min(value / 3.0, 1.0)

    @staticmethod
    def normalize_skill_name(value: str) -> str:
        return canonicalize_skill_key(value)

    @staticmethod
    def _importance_label(importance: float) -> str:
        normalized = AnalysisService.normalize_importance(importance)
        if normalized >= 0.75:
            return "high"
        if normalized >= 0.45:
            return "medium"
        return "low"

    @staticmethod
    def _build_job_skill_map(payload: GapAnalysisRequest) -> Dict[str, object]:
        job_skill_map: Dict[str, object] = {}
        for skill in payload.job_skills:
            if is_non_skill_role_label(skill.name):
                continue
            for skill_name in expand_skill_labels(skill.name):
                if is_non_skill_role_label(skill_name):
                    continue
                key = AnalysisService.normalize_skill_name(skill_name)
                if key and key not in job_skill_map:
                    copied = skill.model_copy(update={"name": skill_name})
                    job_skill_map[key] = copied
        return job_skill_map

    @staticmethod
    def generate_gap_analysis(payload: GapAnalysisRequest) -> GapAnalysisResponse:
        cv_skill_map = {
            AnalysisService.normalize_skill_name(skill.name): skill
            for skill in payload.cv_skills
            if not is_non_skill_role_label(skill.name)
        }
        job_skill_map = AnalysisService._build_job_skill_map(payload)

        missing: List[MissingSkillGap] = []
        weak: List[WeakSkillGap] = []

        for skill_name, job_skill in job_skill_map.items():
            cv_skill = cv_skill_map.get(skill_name)
            label = AnalysisService._importance_label(job_skill.importance)

            if not cv_skill:
                missing.append(
                    MissingSkillGap(
                        skill=job_skill.name,
                        importance=label,
                        reason=f"JD yêu cầu kỹ năng này với mức độ quan trọng {AnalysisService.normalize_importance(job_skill.importance):.2f}.",
                    )
                )
                continue

            gap_value = max(job_skill.required_proficiency - cv_skill.proficiency, 0)
            if gap_value > 0:
                weak.append(
                    WeakSkillGap(
                        skill=job_skill.name,
                        current_proficiency=round(cv_skill.proficiency, 3),
                        required_proficiency=round(job_skill.required_proficiency, 3),
                        gap=round(gap_value, 3),
                    )
                )

        exp_gap_years = max(payload.job_years_required - payload.cv_years_experience, 0)
        exp_gap = ExperienceGap(
            required_years=payload.job_years_required,
            current_years=payload.cv_years_experience,
            gap_weeks=round(exp_gap_years * 52),
        )

        cv_level = payload.cv_level.strip().lower()
        job_level = payload.job_level.strip().lower()
        cv_level_rank = LEVEL_MAP.get(cv_level, 1)
        job_level_rank = LEVEL_MAP.get(job_level, 1)

        level_gap = LevelGap(
            cv_level=cv_level,
            job_level=job_level,
            gap_levels=abs(job_level_rank - cv_level_rank),
        )

        required_certs = {value.strip() for value in payload.job_certifications if value.strip()}
        current_certs = {value.strip() for value in payload.cv_certifications if value.strip()}
        missing_certs = sorted(required_certs - current_certs)

        cert_gap = CertificationGap(
            required=sorted(required_certs),
            have=sorted(current_certs),
            missing=missing_certs,
        )

        recommended_skills = [item.skill for item in missing]
        for item in sorted(weak, key=lambda value: value.gap, reverse=True):
            if item.skill not in recommended_skills:
                recommended_skills.append(item.skill)

        return GapAnalysisResponse(
            skillGap=SkillGap(missing=missing, weak=weak),
            experienceGap=exp_gap,
            levelGap=level_gap,
            certificationGap=cert_gap,
            recommendedSkills=recommended_skills,
        )
