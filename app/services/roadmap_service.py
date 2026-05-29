from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Dict, List

from app.schemas.roadmap import (
    RecommendedResource,
    RoadmapGenerateRequest,
    RoadmapGenerateResponse,
    RoadmapPhase,
    RoadmapSkillItem,
)
from app.services.skill_normalization import normalize_skill_key


class RoadmapService:
    HOURS_PER_WEEK = 8

    @staticmethod
    def _priority_from_importance(importance: str) -> int:
        if importance == "high":
            return 5
        if importance == "medium":
            return 3
        return 2

    @staticmethod
    def _weeks_from_importance(importance: str) -> int:
        if importance == "high":
            return 4
        if importance == "medium":
            return 3
        return 2

    @staticmethod
    def _weeks_from_gap(gap_value: float) -> int:
        if gap_value >= 0.5:
            return 4
        if gap_value >= 0.3:
            return 3
        return 2

    @staticmethod
    def _priority_from_weak_skill(
        gap_value: float,
        current_proficiency: float,
        required_proficiency: float,
        transfer_bonus: float,
        transfer_direction_factor: float,
    ) -> int:
        normalized_gap = max(0.0, min(gap_value, 1.0))
        normalized_current = max(0.0, min(current_proficiency, 1.0))
        normalized_required = max(0.0, min(required_proficiency, 1.0))
        normalized_transfer = max(0.0, min(transfer_bonus, 0.6))
        normalized_direction = max(0.3, min(transfer_direction_factor, 1.5))

        transfer_relief = min(0.18, normalized_transfer * normalized_direction * 0.3)
        severity_score = (
            (normalized_gap * 0.6)
            + (normalized_required * 0.25)
            + ((1.0 - normalized_current) * 0.15)
            - transfer_relief
        )

        if severity_score >= 0.78:
            return 5
        if severity_score >= 0.58:
            return 4
        if severity_score >= 0.38:
            return 3
        return 2

    @staticmethod
    def _estimated_hours_and_weeks(
        baseline_hours: int | None,
        importance: str,
        gap_value: float,
        transfer_bonus: float,
        transfer_direction_factor: float,
        fallback_weeks: int,
    ) -> tuple[int | None, int, float]:
        if baseline_hours is None or baseline_hours <= 0:
            return None, max(1, fallback_weeks), 0.0

        normalized_gap = max(0.0, min(gap_value, 1.0))
        normalized_transfer = max(0.0, min(transfer_bonus, 0.6))
        normalized_direction = max(0.3, min(transfer_direction_factor, 1.5))

        importance_factor = 1.15 if importance == "high" else 1.0 if importance == "medium" else 0.9
        gap_factor = 1.0 + (0.7 * normalized_gap)
        effective_transfer_bonus = min(0.6, normalized_transfer * normalized_direction)
        transfer_factor = 1.0 - effective_transfer_bonus

        adjusted_hours = max(6, round(float(baseline_hours) * importance_factor * gap_factor * transfer_factor))
        weeks = max(1, math.ceil(adjusted_hours / RoadmapService.HOURS_PER_WEEK))
        return adjusted_hours, weeks, effective_transfer_bonus

    @staticmethod
    def _resource_map(request: RoadmapGenerateRequest) -> Dict[str, List[RecommendedResource]]:
        skill_resource_map: Dict[str, List[RecommendedResource]] = {}
        for resource in request.resources:
            key = normalize_skill_key(resource.skill_name)
            if key not in skill_resource_map:
                skill_resource_map[key] = []
            skill_resource_map[key].append(
                RecommendedResource(
                    title=resource.title,
                    provider=resource.provider,
                    url=resource.url,
                    duration_hours=resource.duration_hours,
                )
            )
        return skill_resource_map

    @staticmethod
    def generate(request: RoadmapGenerateRequest) -> RoadmapGenerateResponse:
        skill_resource_map = RoadmapService._resource_map(request)
        skill_items: List[RoadmapSkillItem] = []

        for item in request.missing_skills:
            key = normalize_skill_key(item.skill)
            default_weeks = RoadmapService._weeks_from_importance(item.importance)
            adjusted_hours, estimated_weeks, effective_transfer_bonus = RoadmapService._estimated_hours_and_weeks(
                baseline_hours=item.baseline_hours,
                importance=item.importance,
                gap_value=1.0,
                transfer_bonus=item.transfer_bonus,
                transfer_direction_factor=item.transfer_direction_factor,
                fallback_weeks=default_weeks,
            )
            skill_items.append(
                RoadmapSkillItem(
                    skill_name=item.skill,
                    priority=RoadmapService._priority_from_importance(item.importance),
                    estimated_weeks=estimated_weeks,
                    baseline_hours=item.baseline_hours,
                    transfer_bonus=item.transfer_bonus,
                    transfer_direction_factor=item.transfer_direction_factor,
                    effective_transfer_bonus=effective_transfer_bonus,
                    adjusted_hours=adjusted_hours,
                    recommended_resources=skill_resource_map.get(key, []),
                )
            )

        for item in sorted(request.weak_skills, key=lambda value: value.gap, reverse=True):
            if any(normalize_skill_key(existing.skill_name) == normalize_skill_key(item.skill) for existing in skill_items):
                continue
            key = normalize_skill_key(item.skill)
            default_weeks = RoadmapService._weeks_from_gap(item.gap)
            adjusted_hours, estimated_weeks, effective_transfer_bonus = RoadmapService._estimated_hours_and_weeks(
                baseline_hours=item.baseline_hours,
                importance="medium",
                gap_value=item.gap,
                transfer_bonus=item.transfer_bonus,
                transfer_direction_factor=item.transfer_direction_factor,
                fallback_weeks=default_weeks,
            )
            skill_items.append(
                RoadmapSkillItem(
                    skill_name=item.skill,
                    priority=RoadmapService._priority_from_weak_skill(
                        gap_value=item.gap,
                        current_proficiency=item.current_proficiency,
                        required_proficiency=item.required_proficiency,
                        transfer_bonus=item.transfer_bonus,
                        transfer_direction_factor=item.transfer_direction_factor,
                    ),
                    estimated_weeks=estimated_weeks,
                    baseline_hours=item.baseline_hours,
                    transfer_bonus=item.transfer_bonus,
                    transfer_direction_factor=item.transfer_direction_factor,
                    effective_transfer_bonus=effective_transfer_bonus,
                    adjusted_hours=adjusted_hours,
                    recommended_resources=skill_resource_map.get(key, []),
                )
            )

        skill_items.sort(key=lambda value: (value.priority, value.estimated_weeks), reverse=True)

        phases: List[RoadmapPhase] = []
        if not skill_items:
            completion = date.today() + timedelta(weeks=1)
            return RoadmapGenerateResponse(
                phases=[],
                total_weeks=0,
                estimated_completion=completion,
                difficulty_level="LOW",
            )

        total_weeks = 0
        phase_index = 1
        for start in range(0, len(skill_items), request.max_skills_per_phase):
            phase_skills = skill_items[start : start + request.max_skills_per_phase]
            phase_duration = sum(item.estimated_weeks for item in phase_skills)
            total_weeks += phase_duration
            phases.append(
                RoadmapPhase(
                    phase=phase_index,
                    duration_weeks=phase_duration,
                    title=f"Giai đoạn {phase_index}",
                    skills=phase_skills,
                )
            )
            phase_index += 1

        if request.timeframe_weeks > 0:
            total_weeks = min(total_weeks, request.timeframe_weeks)
        completion = date.today() + timedelta(weeks=total_weeks)

        if total_weeks <= 12:
            difficulty = "LOW"
        elif total_weeks <= 26:
            difficulty = "MEDIUM"
        else:
            difficulty = "HIGH"

        return RoadmapGenerateResponse(
            phases=phases,
            total_weeks=total_weeks,
            estimated_completion=completion,
            difficulty_level=difficulty,
        )

