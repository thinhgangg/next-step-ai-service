from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.analyzer import GapAnalysisResponse, JobMatchResponse, JobSkillInput, CvSkillInput
from app.schemas.roadmap import RoadmapGenerateResponse


class CvIngestRequest(BaseModel):
	cv_text: str = Field(..., min_length=30)
	job_id: Optional[int] = Field(default=None, ge=1)
	job_url: Optional[str] = None
	timeframe_weeks: int = Field(0, ge=0)
	max_skills_per_phase: int = Field(4, ge=1, le=5)

	@model_validator(mode="after")
	def validate_job_reference(self) -> "CvIngestRequest":
		if not self.job_id and not self.job_url:
			raise ValueError("Either job_id or job_url is required")
		return self


class ExtractedCvProfile(BaseModel):
	cv_level: str
	cv_years_experience: float
	preferred_locations: List[str] = Field(default_factory=list)
	cv_certifications: List[str] = Field(default_factory=list)
	cv_skills: List[CvSkillInput] = Field(default_factory=list)


class JobContext(BaseModel):
	job_id: int
	title: str
	source_url: str
	job_level: str
	job_years_required: float
	job_location: Optional[str] = None
	job_is_remote: bool = False
	job_skills: List[JobSkillInput] = Field(default_factory=list)


class AiCvReview(BaseModel):
	summary: str
	strengths: List[str] = Field(default_factory=list)
	concerns: List[str] = Field(default_factory=list)
	recommendations: List[str] = Field(default_factory=list)
	verdict: str
	source: str = "ai"


class CvIngestResponse(BaseModel):
	analysis_result_id: Optional[int] = None
	extracted_profile: ExtractedCvProfile
	job_context: JobContext
	job_match: JobMatchResponse
	gap_analysis: GapAnalysisResponse
	roadmap: RoadmapGenerateResponse
	ai_review: Optional[AiCvReview] = None


class AnalysisHistoryItem(BaseModel):
	analysis_id: int
	job_id: int
	job_title: str
	cv_filename: Optional[str] = None
	created_at: datetime
	job_match_score: Optional[int] = None
	roadmap_total_weeks: Optional[int] = None


class AnalysisHistoryResponse(BaseModel):
	total: int
	items: List[AnalysisHistoryItem] = Field(default_factory=list)
