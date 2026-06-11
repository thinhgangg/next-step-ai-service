from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import desc
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import date, datetime, timezone
from threading import Lock
import json
import re

from app.db.base_class import Base
from app.db.session import engine
from app.db.session import get_db
from app.models.company import Company
from app.models.cv_analysis_result import CvAnalysisResult
from app.models.cv_skill import CvSkill
from app.models.job_upload import JobUpload
from app.models.job import Job, JobLevel, JobStatus
from app.models.skill_course import SkillCourse
from app.models.skill import Skill
from app.models.skill_gap import SkillGap
from app.schemas.analyzer import GapAnalysisRequest
from app.schemas.cv import AnalysisHistoryItem, AnalysisHistoryResponse, CvIngestRequest, CvIngestResponse
from app.schemas.roadmap import (
    MissingSkillInput,
    ResourceInput,
    RoadmapGenerateRequest,
    WeakSkillInput,
)
from app.services.analysis_service import AnalysisService
from app.services.ai_service import AIService
from app.services.embedding_service import EmbeddingService
from app.services.job_matching_service import JobMatchingService
from app.services.learning_duration_service import LearningDurationService
from app.services.pdf_processor import CvIngestService
from app.services.roadmap_service import RoadmapService
from app.services.skill_normalization import normalize_skill_key

router = APIRouter()

_RELATION_FILE = Path(__file__).resolve().parents[3] / "data" / "skill_relation_groups.json"
_RAG_TOP_K = 5
_ANALYSIS_TABLES_READY = False
_ANALYSIS_TABLES_LOCK = Lock()


def _infer_cv_title(cv_text: str) -> str | None:
    if not cv_text:
        return None
    lines = [line.strip() for line in re.split(r"[\r\n]+", cv_text) if line.strip()]
    if lines:
        return lines[0][:120]

    fallback = cv_text.strip()
    return fallback[:120] if fallback else None


def _estimate_ats_score(cv_text: str) -> float:
    text = (cv_text or "").lower()
    score = 0.0
    if len(text) >= 300:
        score += 0.3
    if any(token in text for token in ["experience", "kinh nghiệm"]):
        score += 0.25
    if any(token in text for token in ["skills", "kỹ năng"]):
        score += 0.25
    if any(token in text for token in ["education", "học vấn"]):
        score += 0.2
    return min(1.0, score)


def _build_rag_query(cv_text: str, job_context, gap_result) -> str:
    parts: list[str] = []

    if getattr(job_context, "title", None):
        parts.append(f"Job title: {job_context.title}")
    if getattr(job_context, "job_level", None):
        parts.append(f"Level: {job_context.job_level}")
    if getattr(job_context, "job_location", None):
        parts.append(f"Location: {job_context.job_location}")

    job_skills = [
        getattr(item, "name", "")
        for item in getattr(job_context, "job_skills", []) or []
        if getattr(item, "name", "")
    ]
    if job_skills:
        parts.append(f"Required skills: {', '.join(job_skills[:12])}")

    missing_skills = [item.skill for item in getattr(gap_result.skillGap, "missing", [])[:12]]
    if missing_skills:
        parts.append(f"Missing skills: {', '.join(missing_skills)}")

    weak_skills = [item.skill for item in getattr(gap_result.skillGap, "weak", [])[:12]]
    if weak_skills:
        parts.append(f"Weak skills: {', '.join(weak_skills)}")

    cv_excerpt = (cv_text or "").strip().replace("\n", " ")
    if cv_excerpt:
        parts.append(f"CV excerpt: {cv_excerpt[:1000]}")

    return " | ".join(part for part in parts if part.strip())


def _format_retrieved_contexts(chunks) -> list[dict]:
    formatted: list[dict] = []
    for chunk in chunks or []:
        content = str(chunk.get("content") or "").strip()
        if not content:
            continue
        formatted.append(
            {
                "source_type": chunk.get("source_type"),
                "source_id": chunk.get("source_id"),
                "chunk_index": chunk.get("chunk_index"),
                "similarity": chunk.get("similarity"),
                "content": content[:800],
                "metadata": chunk.get("metadata") or {},
            }
        )
    return formatted


def _safe_retrieve_contexts(db: Session, rag_query: str) -> list[dict]:
    try:
        return _format_retrieved_contexts(
            EmbeddingService.retrieve_similar_chunks(db, rag_query, top_k=_RAG_TOP_K)
        )
    except Exception:
        return []


def _clean_optional_text(value) -> str | None:
    text_value = str(value or "").strip()
    return text_value or None


def _clean_db_string(value, max_length: int, blank_if_too_long: bool = False) -> str | None:
    text_value = _clean_optional_text(value)
    if not text_value:
        return None
    if len(text_value) <= max_length:
        return text_value
    if blank_if_too_long:
        return None
    return text_value[:max_length]


def _parse_optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _parse_optional_date(value) -> date | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    try:
        return date.fromisoformat(text_value[:10])
    except ValueError:
        return None


def _jd_contains(jd_text: str, value: str | None) -> bool:
    text_value = str(value or "").strip()
    if not text_value:
        return False
    normalized_jd = re.sub(r"\s+", " ", (jd_text or "")).lower()
    normalized_value = re.sub(r"\s+", " ", text_value).lower()
    return normalized_value in normalized_jd


def _clean_job_title(value, jd_text: str, fallback: str) -> str:
    title = _clean_db_string(value, 255)
    if title and not _jd_contains(jd_text, title):
        title = None
    if not title:
        title = _clean_db_string(fallback, 255)
    if not title:
        return "Uploaded JD"

    noisy_markers = ["resume", "curriculum vitae", "education", "project", "candidate"]
    if any(marker in title.lower() for marker in noisy_markers):
        return "Uploaded JD"

    return title


def _clean_company_name(value, jd_text: str) -> str | None:
    company = _clean_db_string(value, 255)
    if not company:
        return None
    if _jd_contains(jd_text, company):
        return company
    return None


def _normalize_employment_type(value, jd_text: str) -> str | None:
    raw = f"{value or ''} {jd_text or ''}".lower()
    patterns = [
        ("Fulltime", ["fulltime", "full-time", "full time"]),
        ("Part-time", ["part-time", "part time"]),
        ("Contract", ["contract", "contractor"]),
        ("Internship", ["internship", "intern"]),
        ("Hybrid", ["hybrid"]),
        ("Freelance", ["freelance"]),
        ("Remote", ["remote", "work from home"]),
    ]
    for label, aliases in patterns:
        if any(alias in raw for alias in aliases):
            return label
    return None


def _extract_experience_label(jd_text: str, ai_value) -> tuple[str | None, float]:
    text = str(jd_text or "")
    ai_text = str(ai_value or "").strip()
    candidates = re.findall(
        r"(\d+(?:[\.,]\d+)?)\s*\+?\s*(?:years?|yrs?|năm)\s*(?:of\s+)?(?:experience|exp)?",
        f"{ai_text} {text}",
        flags=re.IGNORECASE,
    )
    if not candidates:
        return None, 0.0
    values = [float(item.replace(",", ".")) for item in candidates]
    years = max(values)
    label = f"{years:g}+ years"
    return label, years


def _extract_salary(jd_text: str, ai_min, ai_max, ai_currency) -> tuple[int | None, int | None, str | None]:
    salary_min = _parse_optional_int(ai_min)
    salary_max = _parse_optional_int(ai_max)
    currency = _clean_optional_text(ai_currency)
    currency = currency.upper() if currency else None
    if currency not in {"VND", "USD"}:
        currency = None

    text = str(jd_text or "")
    usd_match = re.search(r"\$\s*(\d+(?:[\.,]\d+)?)\s*(?:-|–|to)?\s*\$?\s*(\d+(?:[\.,]\d+)?)?", text, flags=re.IGNORECASE)
    if usd_match:
        salary_min = int(float(usd_match.group(1).replace(",", "")))
        salary_max = int(float((usd_match.group(2) or usd_match.group(1)).replace(",", "")))
        currency = "USD"

    vnd_match = re.search(
        r"(\d+(?:[\.,]\d+)?)\s*(?:-|–|to|đến)?\s*(\d+(?:[\.,]\d+)?)?\s*(?:triệu|tr|million|vnd|₫)",
        text,
        flags=re.IGNORECASE,
    )
    if vnd_match and currency != "USD":
        left = float(vnd_match.group(1).replace(",", "."))
        right = float((vnd_match.group(2) or vnd_match.group(1)).replace(",", "."))
        multiplier = 1_000_000 if re.search(r"triệu|tr|million", vnd_match.group(0), flags=re.IGNORECASE) else 1
        salary_min = int(left * multiplier)
        salary_max = int(right * multiplier)
        currency = "VND"

    return salary_min, salary_max, currency


def _section_or_none(ai_value, jd_text: str, max_length: int = 5000) -> str | None:
    text_value = _clean_optional_text(ai_value)
    if not text_value:
        return None
    noisy_markers = ["resume", "curriculum vitae", "education", "cv_", "extracted_profile"]
    if any(marker in text_value.lower() for marker in noisy_markers):
        return None
    return text_value[:max_length]


def _fallback_cv_review(match_result, gap_result, roadmap_result) -> dict:
    score = int(match_result.score or 0)
    if score >= 75:
        verdict = "strong_match"
        summary = "CV đang phù hợp khá tốt với job, có thể ưu tiên tinh chỉnh cách trình bày và bằng chứng dự án."
    elif score >= 45:
        verdict = "potential_match"
        summary = "CV có nền tảng phù hợp nhưng vẫn cần bổ sung một số kỹ năng hoặc bằng chứng để sát yêu cầu job hơn."
    else:
        verdict = "weak_match"
        summary = "CV hiện còn lệch khá nhiều so với job, nên tập trung học các kỹ năng thiếu trước khi ứng tuyển."

    matched = list(match_result.matchedSkills or [])[:4]
    missing = [item.skill for item in gap_result.skillGap.missing[:4]]
    weak = [item.skill for item in gap_result.skillGap.weak[:4]]

    strengths = [f"Đã khớp với yêu cầu: {', '.join(matched)}."] if matched else []
    concerns = []
    if missing:
        concerns.append(f"Còn thiếu: {', '.join(missing)}.")
    if weak:
        concerns.append(f"Cần cải thiện mức độ thành thạo: {', '.join(weak)}.")
    if roadmap_result.total_weeks > 0:
        concerns.append(f"Roadmap ước tính cần khoảng {roadmap_result.total_weeks} tuần.")

    recommendations = []
    for skill in [*missing, *weak][:4]:
        recommendations.append(f"Bổ sung project hoặc kinh nghiệm thực tế liên quan đến {skill}.")
    if not recommendations:
        recommendations.append("Tối ưu CV bằng số liệu, kết quả dự án và mô tả vai trò cụ thể hơn.")

    return {
        "summary": summary,
        "strengths": strengths[:4],
        "concerns": concerns[:4],
        "recommendations": recommendations[:4],
        "verdict": verdict,
        "source": "fallback",
    }


def _ensure_analysis_table() -> None:
    global _ANALYSIS_TABLES_READY
    if _ANALYSIS_TABLES_READY:
        return

    with _ANALYSIS_TABLES_LOCK:
        if _ANALYSIS_TABLES_READY:
            return

        try:
            Base.metadata.create_all(
                bind=engine,
                tables=[
                    CvAnalysisResult.__table__,
                    CvSkill.__table__,
                    JobUpload.__table__,
                    SkillGap.__table__,
                ],
            )
        except SQLAlchemyError as exc:
            engine.dispose()
            raise HTTPException(
                status_code=503,
                detail="Database schema is not ready. Run `alembic upgrade head` and retry.",
            ) from exc

        _ANALYSIS_TABLES_READY = True


def _persist_uploaded_jd_job(
    db: Session,
    job_context,
    jd_text: str,
    jd_filename: str | None,
) -> Job:
    company = db.query(Company).filter(Company.name == "Uploaded JD").one_or_none()
    if not company:
        company = Company(name="Uploaded JD")
        db.add(company)
        db.flush()

    level = None
    try:
        level = JobLevel(job_context.job_level)
    except ValueError:
        level = None

    timestamp = datetime.now(timezone.utc)
    source_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", jd_filename or "job-description").strip("-")
    source_url = f"uploaded://jd/{int(timestamp.timestamp() * 1000)}-{source_name or 'job-description'}"
    cleaned_jd_text = CvIngestService._clean_text(jd_text)

    job = Job(
        company_company_id=company.company_id,
        title=job_context.title or "Uploaded JD",
        level=level,
        location=job_context.job_location,
        description_raw=jd_text or "Uploaded JD",
        description_clean=cleaned_jd_text or None,
        skills_qualifications=cleaned_jd_text or None,
        source_url=source_url[:1000],
        source_site="uploaded_jd",
        scraped_at=timestamp,
        status=JobStatus.active,
    )
    db.add(job)
    db.flush()
    return job


def _build_skill_lookup(db: Session) -> tuple[dict[str, Skill], dict[str, Skill]]:
    skills = db.query(Skill).all()
    by_name: dict[str, Skill] = {}
    by_alias: dict[str, Skill] = {}

    for skill in skills:
        if not skill.name:
            continue
        name_key = normalize_skill_key(skill.name)
        if name_key:
            by_name[name_key] = skill

        aliases = skill.aliases or []
        for alias in aliases:
            alias_key = normalize_skill_key(alias)
            if alias_key and alias_key not in by_alias:
                by_alias[alias_key] = skill

    return by_name, by_alias


def _label_to_priority(label: str) -> float:
    normalized = (label or "").strip().lower()
    if normalized == "high":
        return 0.9
    if normalized == "medium":
        return 0.72
    return 0.55


def _weak_gap_priority(gap: float, current_proficiency: float, required_proficiency: float) -> float:
    clamped_gap = max(0.0, min(1.0, float(gap)))
    clamped_required = max(0.0, min(1.0, float(required_proficiency)))
    clamped_current = max(0.0, min(1.0, float(current_proficiency)))

    base_score = 0.35 + (clamped_gap * 0.45)
    required_bonus = clamped_required * 0.12
    current_penalty = clamped_current * 0.05

    return round(max(0.35, min(0.95, base_score + required_bonus - current_penalty)), 3)


def _load_skill_groups() -> list[set[str]]:
    try:
        if not _RELATION_FILE.exists():
            return []
        payload = json.loads(_RELATION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    groups: list[set[str]] = []
    for item in payload.get("groups", []):
        skills = {
            normalize_skill_key(name)
            for name in item.get("skills", [])
            if normalize_skill_key(name)
        }
        if len(skills) >= 2:
            groups.append(skills)
    return groups


def _build_duration_and_resource_index(db: Session) -> tuple[dict[str, int], dict[str, list[ResourceInput]]]:
    try:
        rows = (
            db.query(SkillCourse, Skill)
            .join(Skill, Skill.skill_id == SkillCourse.skill_id)
            .all()
        )
    except SQLAlchemyError as exc:
        db.rollback()
        print(f"--- ROADMAP RESOURCE DB FALLBACK: {exc} ---")
        return {}, {}

    baseline_hours: dict[str, int] = {}
    resources: dict[str, list[ResourceInput]] = {}

    for row_course, row_skill in rows:
        if not row_skill or not row_skill.name:
            continue
        key = normalize_skill_key(row_skill.name)
        if not key:
            continue

        if row_course.duration_hours is not None and row_course.duration_hours > 0:
            baseline_hours[key] = max(int(row_course.duration_hours), baseline_hours.get(key, 0))

        title = (row_course.title or "").strip()
        if not title:
            continue
        bucket = resources.setdefault(key, [])
        if len(bucket) >= 3:
            continue

        bucket.append(
            ResourceInput(
                skill_name=row_skill.name,
                title=title,
                provider=row_course.platform,
                url=row_course.url,
                duration_hours=row_course.duration_hours,
            )
        )

    return baseline_hours, resources


def _skill_hours(skill_name: str, baseline_hours_map: dict[str, int]) -> int:
    key = normalize_skill_key(skill_name)
    if key in baseline_hours_map:
        return int(baseline_hours_map[key])

    hours, _, _ = LearningDurationService.get_reference_baseline(skill_name)
    return int(hours)


def _directional_factor(source_hours: int, target_hours: int) -> float:
    if source_hours <= 0 or target_hours <= 0:
        return 1.0

    ratio = source_hours / target_hours
    if ratio < 0.55:
        return 0.55
    if ratio > 1.25:
        return 1.25
    return round(ratio, 3)


def _transfer_bonus(
    skill_name: str,
    cv_skills: set[str],
    groups: list[set[str]],
    baseline_hours_map: dict[str, int],
) -> tuple[float, float]:
    target = normalize_skill_key(skill_name)
    if not target or not cv_skills:
        return 0.0, 1.0

    if target in cv_skills:
        return 0.0, 1.0

    target_hours = _skill_hours(target, baseline_hours_map)
    best_effective = 0.0
    best_base = 0.0
    best_direction = 1.0
    for group in groups:
        if target not in group:
            continue
        related = sorted((group - {target}).intersection(cv_skills))
        if not related:
            continue

        ratio = len(related) / max(1, len(group) - 1)
        base_score = min(0.45, 0.18 + (0.30 * ratio))

        for source in related:
            source_hours = _skill_hours(source, baseline_hours_map)
            direction = _directional_factor(source_hours, target_hours)
            score = round(base_score * direction, 3)
            if score > best_effective:
                best_effective = score
                best_base = base_score
                best_direction = direction

    return round(best_base, 3), round(best_direction, 3)


def _persist_analysis_details(
    db: Session,
    analysis_id: int,
    extracted,
    gap_result,
) -> None:
    skill_by_name, skill_by_alias = _build_skill_lookup(db)

    cv_rows: list[CvSkill] = []
    for item in extracted.cv_skills:
        raw_name = (item.name or "").strip()
        if not raw_name:
            continue

        key = normalize_skill_key(raw_name)
        matched_skill = skill_by_name.get(key)
        confidence = 1.0

        if not matched_skill:
            matched_skill = skill_by_alias.get(key)
            confidence = 0.7 if matched_skill else 0.5

        if not matched_skill:
            continue

        cv_rows.append(
            CvSkill(
                analysis_id=analysis_id,
                skill_id=matched_skill.skill_id,
                confidence=confidence,
                source="regex",
            )
        )

    if cv_rows:
        db.add_all(cv_rows)

    gap_rows_by_skill: dict[int, SkillGap] = {}

    for item in gap_result.skillGap.missing:
        skill_name = normalize_skill_key(item.skill)
        matched_skill = skill_by_name.get(skill_name) or skill_by_alias.get(skill_name)
        if not matched_skill:
            continue

        priority = _label_to_priority(item.importance)
        gap_row = SkillGap(
            analysis_id=analysis_id,
            skill_id=matched_skill.skill_id,
            priority_score=priority,
            gap_reason=item.reason,
        )
        existing = gap_rows_by_skill.get(matched_skill.skill_id)
        if not existing or gap_row.priority_score > existing.priority_score:
            gap_rows_by_skill[matched_skill.skill_id] = gap_row

    for item in gap_result.skillGap.weak:
        skill_name = normalize_skill_key(item.skill)
        matched_skill = skill_by_name.get(skill_name) or skill_by_alias.get(skill_name)
        if not matched_skill:
            continue

        priority = _weak_gap_priority(
            gap=item.gap,
            current_proficiency=item.current_proficiency,
            required_proficiency=item.required_proficiency,
        )
        reason = f"Current {item.current_proficiency:.2f}, required {item.required_proficiency:.2f}, gap {item.gap:.2f}"
        gap_row = SkillGap(
            analysis_id=analysis_id,
            skill_id=matched_skill.skill_id,
            priority_score=priority,
            gap_reason=reason,
        )

        existing = gap_rows_by_skill.get(matched_skill.skill_id)
        if not existing or gap_row.priority_score > existing.priority_score:
            gap_rows_by_skill[matched_skill.skill_id] = gap_row

    if gap_rows_by_skill:
        db.add_all(list(gap_rows_by_skill.values()))

    db.commit()


def _run_analysis(
    db: Session,
    cv_text: str,
    job_id: int | None,
    job_url: str | None,
    timeframe_weeks: int,
    max_skills_per_phase: int,
    cv_filename: str | None = None,
    user_id: int | None = None,
    cv_id: int | None = None,
) -> CvIngestResponse:
    try:
        extracted = CvIngestService.extract_profile(db, cv_text)
        job = CvIngestService.find_job(db, job_id, job_url)
        job_context = CvIngestService.build_job_context(job)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to process CV: {exc}") from exc

    analysis_payload = GapAnalysisRequest(
        cv_skills=extracted.cv_skills,
        job_skills=job_context.job_skills,
        cv_years_experience=extracted.cv_years_experience,
        job_years_required=job_context.job_years_required,
        cv_level=extracted.cv_level,
        job_level=job_context.job_level,
        preferred_locations=extracted.preferred_locations,
        job_location=job_context.job_location,
        job_is_remote=job_context.job_is_remote,
        cv_certifications=extracted.cv_certifications,
        job_certifications=[],
        cv_title=_infer_cv_title(cv_text),
        job_title=job_context.title,
        ats_parse_score=_estimate_ats_score(cv_text),
    )

    match_result = JobMatchingService.calculate_job_match(analysis_payload)
    gap_result = AnalysisService.generate_gap_analysis(analysis_payload)

    relation_groups = _load_skill_groups()
    baseline_hours_map, resource_map = _build_duration_and_resource_index(db)
    cv_skill_set = {
        normalize_skill_key(item.name)
        for item in extracted.cv_skills
        if normalize_skill_key(item.name)
    }

    roadmap_resources: list[ResourceInput] = []
    for values in resource_map.values():
        roadmap_resources.extend(values)

    missing_inputs: list[MissingSkillInput] = []
    for item in gap_result.skillGap.missing:
        key = normalize_skill_key(item.skill)
        transfer_bonus, direction_factor = _transfer_bonus(item.skill, cv_skill_set, relation_groups, baseline_hours_map)
        missing_inputs.append(
            MissingSkillInput(
                skill=item.skill,
                importance=item.importance,
                reason=item.reason,
                baseline_hours=baseline_hours_map.get(key),
                transfer_bonus=transfer_bonus,
                transfer_direction_factor=direction_factor,
            )
        )

    weak_inputs: list[WeakSkillInput] = []
    for item in gap_result.skillGap.weak:
        key = normalize_skill_key(item.skill)
        transfer_bonus, direction_factor = _transfer_bonus(item.skill, cv_skill_set, relation_groups, baseline_hours_map)
        weak_inputs.append(
            WeakSkillInput(
                skill=item.skill,
                current_proficiency=item.current_proficiency,
                required_proficiency=item.required_proficiency,
                gap=item.gap,
                baseline_hours=baseline_hours_map.get(key),
                transfer_bonus=transfer_bonus,
                transfer_direction_factor=direction_factor,
            )
        )

    roadmap_request = RoadmapGenerateRequest(
        goal_title=f"Match {job_context.title}",
        timeframe_weeks=timeframe_weeks,
        max_skills_per_phase=max_skills_per_phase,
        missing_skills=missing_inputs,
        weak_skills=weak_inputs,
        resources=roadmap_resources,
    )
    roadmap_result = RoadmapService.generate(roadmap_request)
    rag_query = _build_rag_query(cv_text, job_context, gap_result)
    retrieved_contexts = _safe_retrieve_contexts(db, rag_query)
    ai_review = AIService.generate_cv_job_review(
        cv_text=cv_text,
        extracted_profile=extracted.model_dump(mode="json"),
        job_context=job_context.model_dump(mode="json"),
        job_match=match_result.model_dump(mode="json"),
        gap_analysis=gap_result.model_dump(mode="json"),
        roadmap=roadmap_result.model_dump(mode="json"),
        retrieved_contexts=retrieved_contexts,
    ) or _fallback_cv_review(match_result, gap_result, roadmap_result)

    _ensure_analysis_table()
    analysis_result = CvAnalysisResult(
        job_job_id=job_context.job_id,
        user_id=user_id,
        cv_id=cv_id,
        cv_filename=cv_filename,
        cv_text_excerpt=(cv_text[:1200] if cv_text else None),
        extracted_profile_json=extracted.model_dump(mode="json"),
        job_context_json=job_context.model_dump(mode="json"),
        job_match_json=match_result.model_dump(mode="json"),
        gap_analysis_json=gap_result.model_dump(mode="json"),
        roadmap_json=roadmap_result.model_dump(mode="json"),
        ai_review_json=ai_review,
    )
    db.add(analysis_result)
    db.commit()
    db.refresh(analysis_result)

    _persist_analysis_details(
        db=db,
        analysis_id=analysis_result.analysis_id,
        extracted=extracted,
        gap_result=gap_result,
    )

    return CvIngestResponse(
        analysis_result_id=analysis_result.analysis_id,
        extracted_profile=extracted,
        job_context=job_context,
        job_match=match_result,
        gap_analysis=gap_result,
        roadmap=roadmap_result,
        ai_review=ai_review,
    )


def _run_uploaded_jd_analysis(
    db: Session,
    cv_text: str,
    jd_text: str,
    timeframe_weeks: int,
    max_skills_per_phase: int,
    jd_filename: str | None = None,
    cv_filename: str | None = None,
    user_id: int | None = None,
    cv_id: int | None = None,
) -> CvIngestResponse:
    try:
        extracted = CvIngestService.extract_profile(db, cv_text)
        job_context = CvIngestService.build_uploaded_job_context(db, jd_text, jd_filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to process uploaded CV/JD: {exc}") from exc

    ai_job = CvIngestService._last_uploaded_job_ai_payload or {}
    rule_title = CvIngestService._infer_job_title(jd_text, jd_filename)
    cleaned_title = _clean_job_title(ai_job.get("title"), jd_text, rule_title)
    company_name = _clean_company_name(ai_job.get("company_name"), jd_text)
    employment_type = _normalize_employment_type(ai_job.get("employment_type"), jd_text)
    experience_label, experience_years = _extract_experience_label(jd_text, ai_job.get("experience"))
    salary_min, salary_max, currency = _extract_salary(
        jd_text,
        ai_job.get("salary_min"),
        ai_job.get("salary_max"),
        ai_job.get("currency"),
    )
    description_clean = CvIngestService._clean_text(jd_text)
    parsed_sections = CvIngestService.extract_uploaded_job_sections(jd_text)
    source_filename = _clean_db_string(jd_filename, 1000)
    source_url = _clean_db_string(f"uploaded://{jd_filename or 'jd'}", 1000)
    job_context = job_context.model_copy(
        update={
            "title": cleaned_title,
            "job_years_required": max(job_context.job_years_required or 0.0, experience_years),
        }
    )

    analysis_payload = GapAnalysisRequest(
        cv_skills=extracted.cv_skills,
        job_skills=job_context.job_skills,
        cv_years_experience=extracted.cv_years_experience,
        job_years_required=job_context.job_years_required,
        cv_level=extracted.cv_level,
        job_level=job_context.job_level,
        preferred_locations=extracted.preferred_locations,
        job_location=job_context.job_location,
        job_is_remote=job_context.job_is_remote,
        cv_certifications=extracted.cv_certifications,
        job_certifications=[],
        cv_title=_infer_cv_title(cv_text),
        job_title=job_context.title,
        ats_parse_score=_estimate_ats_score(cv_text),
    )

    match_result = JobMatchingService.calculate_job_match(analysis_payload)
    gap_result = AnalysisService.generate_gap_analysis(analysis_payload)

    relation_groups = _load_skill_groups()
    baseline_hours_map, resource_map = _build_duration_and_resource_index(db)
    cv_skill_set = {
        normalize_skill_key(item.name)
        for item in extracted.cv_skills
        if normalize_skill_key(item.name)
    }

    roadmap_resources: list[ResourceInput] = []
    for values in resource_map.values():
        roadmap_resources.extend(values)

    missing_inputs: list[MissingSkillInput] = []
    for item in gap_result.skillGap.missing:
        key = normalize_skill_key(item.skill)
        transfer_bonus, direction_factor = _transfer_bonus(item.skill, cv_skill_set, relation_groups, baseline_hours_map)
        missing_inputs.append(
            MissingSkillInput(
                skill=item.skill,
                importance=item.importance,
                reason=item.reason,
                baseline_hours=baseline_hours_map.get(key),
                transfer_bonus=transfer_bonus,
                transfer_direction_factor=direction_factor,
            )
        )

    weak_inputs: list[WeakSkillInput] = []
    for item in gap_result.skillGap.weak:
        key = normalize_skill_key(item.skill)
        transfer_bonus, direction_factor = _transfer_bonus(item.skill, cv_skill_set, relation_groups, baseline_hours_map)
        weak_inputs.append(
            WeakSkillInput(
                skill=item.skill,
                current_proficiency=item.current_proficiency,
                required_proficiency=item.required_proficiency,
                gap=item.gap,
                baseline_hours=baseline_hours_map.get(key),
                transfer_bonus=transfer_bonus,
                transfer_direction_factor=direction_factor,
            )
        )

    roadmap_request = RoadmapGenerateRequest(
        goal_title=f"Match {job_context.title}",
        timeframe_weeks=timeframe_weeks,
        max_skills_per_phase=max_skills_per_phase,
        missing_skills=missing_inputs,
        weak_skills=weak_inputs,
        resources=roadmap_resources,
    )
    roadmap_result = RoadmapService.generate(roadmap_request)
    rag_query = _build_rag_query(cv_text, job_context, gap_result)
    retrieved_contexts = _safe_retrieve_contexts(db, rag_query)
    ai_review = AIService.generate_cv_job_review(
        cv_text=cv_text,
        extracted_profile=extracted.model_dump(mode="json"),
        job_context=job_context.model_dump(mode="json"),
        job_match=match_result.model_dump(mode="json"),
        gap_analysis=gap_result.model_dump(mode="json"),
        roadmap=roadmap_result.model_dump(mode="json"),
        retrieved_contexts=retrieved_contexts,
    ) or _fallback_cv_review(match_result, gap_result, roadmap_result)

    _ensure_analysis_table()

    job_upload = JobUpload(
        company_name=company_name,
        title=cleaned_title,
        level=_clean_db_string(job_context.job_level, 50, blank_if_too_long=True),
        employment_type=employment_type,
        experience=experience_label,
        application_deadline=_parse_optional_date(ai_job.get("application_deadline")),
        location=_clean_db_string(job_context.job_location, 255),
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        description_raw=jd_text,
        description_clean=description_clean,
        role_responsibilities=parsed_sections.get("role_responsibilities")
        or _section_or_none(ai_job.get("role_responsibilities"), jd_text),
        skills_qualifications=parsed_sections.get("skills_qualifications")
        or _section_or_none(ai_job.get("skills_qualifications"), jd_text),
        benefits=parsed_sections.get("benefits")
        or _section_or_none(ai_job.get("benefits"), jd_text),
        source_url=source_url,
        source_site="upload",
        status="uploaded",
        source_filename=source_filename,
        content_excerpt=(jd_text[:1200] if jd_text else None),
        job_context_json=job_context.model_dump(mode="json"),
        job_level=_clean_db_string(job_context.job_level, 50, blank_if_too_long=True),
        job_years_required=job_context.job_years_required,
        job_location=_clean_db_string(job_context.job_location, 255),
        job_is_remote=job_context.job_is_remote,
    )
    try:
        db.add(job_upload)
        db.flush()

        analysis_result = CvAnalysisResult(
            job_job_id=None,
            job_upload_id=job_upload.job_upload_id,
            user_id=user_id,
            cv_id=cv_id,
            cv_filename=cv_filename,
            cv_text_excerpt=(cv_text[:1200] if cv_text else None),
            extracted_profile_json=extracted.model_dump(mode="json"),
            job_context_json=job_context.model_dump(mode="json"),
            job_match_json=match_result.model_dump(mode="json"),
            gap_analysis_json=gap_result.model_dump(mode="json"),
            roadmap_json=roadmap_result.model_dump(mode="json"),
            ai_review_json=ai_review,
        )
        db.add(analysis_result)
        db.commit()
        db.refresh(analysis_result)

        _persist_analysis_details(
            db=db,
            analysis_id=analysis_result.analysis_id,
            extracted=extracted,
            gap_result=gap_result,
        )
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded job analysis: {exc}") from exc

    return CvIngestResponse(
        analysis_result_id=analysis_result.analysis_id,
        extracted_profile=extracted,
        job_context=job_context,
        job_match=match_result,
        gap_analysis=gap_result,
        roadmap=roadmap_result,
        ai_review=ai_review,
    )


@router.post("/ingest", response_model=CvIngestResponse)
def ingest_cv(payload: CvIngestRequest, db: Session = Depends(get_db)) -> CvIngestResponse:
    return _run_analysis(
        db=db,
        cv_text=payload.cv_text,
        job_id=payload.job_id,
        job_url=payload.job_url,
        timeframe_weeks=payload.timeframe_weeks,
        max_skills_per_phase=payload.max_skills_per_phase,
        cv_filename=None,
        user_id=None,
        cv_id=None,
    )


@router.post("/ingest-file", response_model=CvIngestResponse)
async def ingest_cv_file(
    cv_file: UploadFile = File(...),
    job_id: int | None = Form(default=None),
    job_url: str | None = Form(default=None),
    user_id: int | None = Form(default=None),
    cv_id: int | None = Form(default=None),
    timeframe_weeks: int = Form(default=0),
    max_skills_per_phase: int = Form(default=4),
    db: Session = Depends(get_db),
) -> CvIngestResponse:
    if not job_id and not job_url:
        raise HTTPException(status_code=400, detail="Either job_id or job_url is required")

    if timeframe_weeks < 0:
        raise HTTPException(status_code=400, detail="timeframe_weeks must be >= 0 (0 means unlimited)")

    if max_skills_per_phase < 1 or max_skills_per_phase > 5:
        raise HTTPException(status_code=400, detail="max_skills_per_phase must be between 1 and 5")

    try:
        file_bytes = await cv_file.read()
        cv_text = CvIngestService.extract_text_from_file(cv_file.filename or "", cv_file.content_type, file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read CV file: {exc}") from exc

    return _run_analysis(
        db=db,
        cv_text=cv_text,
        job_id=job_id,
        job_url=job_url,
        timeframe_weeks=timeframe_weeks,
        max_skills_per_phase=max_skills_per_phase,
        cv_filename=cv_file.filename,
        user_id=user_id,
        cv_id=cv_id,
    )


@router.post("/scan-upload", response_model=CvIngestResponse)
async def scan_cv_with_uploaded_jd(
    cv_file: UploadFile = File(default=None, description="Resume file: PDF, DOCX, TXT, or MD"),
    jd_file: UploadFile = File(default=None, description="Single JD file: PDF, DOCX, TXT, MD, PNG, JPG, or WebP"),
    jd_files: list[UploadFile | str] = File(default=None, description="Multiple JD files/images. Send repeated jd_files parts"),
    cv_text: str | None = Form(default=None),
    jd_text: str | None = Form(default=None),
    user_id: int | None = Form(default=None),
    cv_id: int | None = Form(default=None),
    timeframe_weeks: int = Form(default=0),
    max_skills_per_phase: int = Form(default=4),
    db: Session = Depends(get_db),
) -> CvIngestResponse:
    if not cv_file and not (cv_text and cv_text.strip()):
        raise HTTPException(status_code=400, detail="Provide cv_file or cv_text")

    uploaded_jd_files = [
        item
        for item in (jd_files or [])
        if isinstance(item, UploadFile) and item.filename
    ]
    if jd_file and jd_file.filename:
        uploaded_jd_files.insert(0, jd_file)

    if not uploaded_jd_files and not (jd_text and jd_text.strip()):
        raise HTTPException(status_code=400, detail="Provide jd_file or jd_text")

    if timeframe_weeks < 0:
        raise HTTPException(status_code=400, detail="timeframe_weeks must be >= 0 (0 means unlimited)")

    if max_skills_per_phase < 1 or max_skills_per_phase > 5:
        raise HTTPException(status_code=400, detail="max_skills_per_phase must be between 1 and 5")

    try:
        final_cv_text = (cv_text or "").strip()
        if cv_file:
            cv_bytes = await cv_file.read()
            final_cv_text = CvIngestService.extract_text_from_file(cv_file.filename or "", cv_file.content_type, cv_bytes)

        jd_text_parts: list[str] = []
        if jd_text and jd_text.strip():
            jd_text_parts.append(jd_text.strip())

        jd_filenames: list[str] = []
        for file_item in uploaded_jd_files:
            jd_filenames.append(file_item.filename or "jd")
            jd_bytes = await file_item.read()
            extracted_jd = CvIngestService.extract_text_from_job_file(
                file_item.filename or "",
                file_item.content_type,
                jd_bytes,
            )
            jd_text_parts.append(extracted_jd)

        final_jd_text = "\n\n".join(part for part in jd_text_parts if part.strip())
        jd_filename = ", ".join(jd_filenames) if jd_filenames else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read uploaded file: {exc}") from exc

    return _run_uploaded_jd_analysis(
        db=db,
        cv_text=final_cv_text,
        jd_text=final_jd_text,
        timeframe_weeks=timeframe_weeks,
        max_skills_per_phase=max_skills_per_phase,
        jd_filename=jd_filename,
        cv_filename=(cv_file.filename if cv_file else None),
        user_id=user_id,
        cv_id=cv_id,
    )


@router.get("/analysis-results", response_model=AnalysisHistoryResponse)
def list_analysis_results(
    user_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> AnalysisHistoryResponse:
    _ensure_analysis_table()
    normalized_limit = max(1, min(limit, 100))
    rows = (
        db.query(CvAnalysisResult)
        .filter(CvAnalysisResult.user_id == user_id)
        .order_by(desc(CvAnalysisResult.created_at), desc(CvAnalysisResult.analysis_id))
        .limit(normalized_limit)
        .all()
    )

    items = [
        AnalysisHistoryItem(
            analysis_id=row.analysis_id,
            user_id=row.user_id,
            cv_id=row.cv_id,
            job_id=row.job_job_id,
            job_upload_id=row.job_upload_id,
            job_source="uploaded" if row.job_upload_id else "crawled",
            job_title=(
                row.job.title
                if row.job
                else row.job_upload.title
                if row.job_upload
                else "Unknown job"
            ),
            cv_filename=row.cv_filename,
            created_at=row.created_at,
            job_match_score=(row.job_match_json or {}).get("score"),
            roadmap_total_weeks=(row.roadmap_json or {}).get("total_weeks"),
        )
        for row in rows
    ]
    return AnalysisHistoryResponse(total=len(items), items=items)


@router.get("/analysis-results/{analysis_id}", response_model=CvIngestResponse)
def get_analysis_result(
    analysis_id: int,
    user_id: int,
    db: Session = Depends(get_db),
) -> CvIngestResponse:
    _ensure_analysis_table()
    row = (
        db.query(CvAnalysisResult)
        .filter(CvAnalysisResult.analysis_id == analysis_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Analysis result not found")
    if row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Analysis result not found")

    return CvIngestResponse(
        analysis_result_id=row.analysis_id,
        extracted_profile=row.extracted_profile_json,
        job_context=row.job_context_json,
        job_match=row.job_match_json,
        gap_analysis=row.gap_analysis_json,
        roadmap=row.roadmap_json,
        ai_review=row.ai_review_json,
    )
