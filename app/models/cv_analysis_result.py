from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class CvAnalysisResult(Base):
    __tablename__ = "cv_analysis_results"

    analysis_id = Column(Integer, primary_key=True, index=True)
    job_job_id = Column(Integer, ForeignKey("jobs.job_id"), nullable=True, index=True)
    job_upload_id = Column(Integer, ForeignKey("job_uploads.job_upload_id"), nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    cv_id = Column(Integer, nullable=True, index=True)
    cv_filename = Column(String(255), nullable=True)
    cv_text_excerpt = Column(Text, nullable=True)

    extracted_profile_json = Column(JSON, nullable=False)
    job_context_json = Column(JSON, nullable=False)
    job_match_json = Column(JSON, nullable=False)
    gap_analysis_json = Column(JSON, nullable=False)
    roadmap_json = Column(JSON, nullable=False)
    ai_review_json = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    job = relationship("Job")
    job_upload = relationship("JobUpload", back_populates="analysis_results")
    cv_skills = relationship("CvSkill", back_populates="analysis", cascade="all, delete-orphan")
    skill_gaps = relationship("SkillGap", back_populates="analysis", cascade="all, delete-orphan")
