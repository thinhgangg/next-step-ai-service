from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.crawler_service import CrawlerService
from app.models.job import Job

router = APIRouter()


class CrawlBatchRequest(BaseModel):
    urls: List[str] = Field(default_factory=list)

@router.post("/crawl")
def crawl_job_api(url: str, db: Session = Depends(get_db)):
    try:
        # Gọi đúng CrawlerService.crawl_job
        job = CrawlerService.crawl_job(db, url) 
        return {"status": "success", "job": job}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/crawl-batch")
def crawl_jobs_batch_api(payload: CrawlBatchRequest, db: Session = Depends(get_db)):
    if not payload.urls:
        raise HTTPException(status_code=400, detail="Danh sách urls không được rỗng")

    try:
        results = CrawlerService.crawl_jobs(db, payload.urls)
        success_count = len([item for item in results if item.get("status") == "success"])
        return {
            "status": "success",
            "total": len(results),
            "success": success_count,
            "failed": len(results) - success_count,
            "results": results,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/")
def list_jobs(db: Session = Depends(get_db), limit: int = 20):
    jobs = db.query(Job).order_by(desc(Job.scraped_at)).limit(limit).all()
    return {
        "total": len(jobs),
        "jobs": [
            {
                "job_id": job.job_id,
                "title": job.title,
                "company_name": job.company.name if job.company else None,
                "location": job.location,
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "currency": job.currency.value if job.currency else None,
                "source_website": job.source_site,
                "source_url": job.source_url,
                "created_at": job.scraped_at,
            }
            for job in jobs
        ],
    }


@router.get("/{job_id}")
def get_job_detail(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job.job_id,
        "title": job.title,
        "company_name": job.company.name if job.company else None,
        "location": job.location,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "currency": job.currency.value if job.currency else None,
        "job_description": job.description_clean or job.description_raw,
        "role_responsibilities": job.role_responsibilities,
        "skills_qualifications": job.skills_qualifications,
        "benefits": job.benefits,
        "job_requirements": [
            item.skill.name
            for item in job.job_skills
            if item.skill and item.skill.name
        ],
        "source_website": job.source_site,
        "source_url": job.source_url,
        "created_at": job.scraped_at,
    }


@router.get("/{job_id}/skills")
def get_job_skills(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.job_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    items = []
    for item in job.job_skills:
        if not item.skill or not item.skill.name:
            continue
        items.append(
            {
                "skill_id": item.skill.skill_id,
                "skill_name": item.skill.name,
                "importance": item.importance,
                "required_proficiency": (
                    0.8
                    if (item.importance or 0) >= 0.8
                    else 0.65
                    if (item.importance or 0) >= 0.5
                    else 0.5
                ),
                "evidence_snippet": item.evidence_snippet,
            }
        )

    items.sort(key=lambda value: (value["importance"] if value["importance"] is not None else 0), reverse=True)

    return {
        "job_id": job.job_id,
        "title": job.title,
        "total_skills": len(items),
        "skills": items,
    }
