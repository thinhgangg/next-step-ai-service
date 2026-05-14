import enum

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class JobLevel(str, enum.Enum):
    intern = "intern"
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"


class Currency(str, enum.Enum):
    VND = "VND"
    USD = "USD"


class JobStatus(str, enum.Enum):
    active = "active"
    closed = "closed"
    draft = "draft"


class Job(Base):
    __tablename__ = "jobs"

    job_id = Column(Integer, primary_key=True, index=True)
    company_company_id = Column(Integer, ForeignKey("companies.company_id"), nullable=False)
    title = Column(String(255), nullable=False)
    level = Column(Enum(JobLevel, name="job_level", create_type=False), nullable=True)
    employment_type = Column(String(50), nullable=True)
    experience = Column(String(50), nullable=True)
    application_deadline = Column(Date, nullable=True)
    location = Column(String(255), nullable=True)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    currency = Column(Enum(Currency, name="currency", create_type=False), nullable=True)
    description_raw = Column(Text, nullable=False)
    description_clean = Column(Text, nullable=True)
    role_responsibilities = Column(Text, nullable=True)
    skills_qualifications = Column(Text, nullable=True)
    benefits = Column(Text, nullable=True)
    source_url = Column(String(1000), nullable=False, unique=True)
    source_site = Column(String(100), nullable=False)
    posted_at = Column(DateTime(timezone=True), nullable=True)
    scraped_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(Enum(JobStatus, name="job_status", create_type=False), nullable=False)

    company = relationship("Company", back_populates="jobs")
    job_skills = relationship("JobSkill", back_populates="job", cascade="all, delete-orphan")
