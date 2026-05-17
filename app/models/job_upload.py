from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class JobUpload(Base):
    __tablename__ = "job_uploads"

    job_upload_id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String(255), nullable=True)
    title = Column(String(255), nullable=False)
    level = Column(String(50), nullable=True)
    employment_type = Column(String(50), nullable=True)
    experience = Column(String(50), nullable=True)
    application_deadline = Column(Date, nullable=True)
    location = Column(String(255), nullable=True)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    currency = Column(String(10), nullable=True)
    description_raw = Column(Text, nullable=False)
    description_clean = Column(Text, nullable=True)
    role_responsibilities = Column(Text, nullable=True)
    skills_qualifications = Column(Text, nullable=True)
    benefits = Column(Text, nullable=True)
    source_url = Column(String(1000), nullable=True)
    source_site = Column(String(100), nullable=False, default="upload")
    posted_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=False, default="uploaded")
    source_filename = Column(String(1000), nullable=True)
    content_excerpt = Column(Text, nullable=True)
    job_context_json = Column(JSON, nullable=False)
    job_level = Column(String(50), nullable=True)
    job_years_required = Column(Float, nullable=True)
    job_location = Column(String(255), nullable=True)
    job_is_remote = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    analysis_results = relationship("CvAnalysisResult", back_populates="job_upload")
