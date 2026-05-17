from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Optional
from zipfile import BadZipFile, ZipFile

import fitz
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.job import Job
from app.models.skill import Skill
from app.schemas.analyzer import CvSkillInput, JobSkillInput
from app.schemas.cv import ExtractedCvProfile, JobContext
from app.services.ai_service import AIService
from app.services.skill_normalization import (
	canonicalize_skill_key,
	expand_skill_labels,
	get_skill_aliases,
	is_non_skill_role_label,
	normalize_skill_key,
)


class CvIngestService:
	_last_uploaded_job_ai_payload: dict = {}

	_fallback_skills = [
		"Python",
		"Java",
		"JavaScript",
		"TypeScript",
		"SQL",
		"PostgreSQL",
		"MySQL",
		"MongoDB",
		"Docker",
		"Kubernetes",
		"AWS",
		"GCP",
		"Azure",
		"React",
		"Vue.js",
		"Node.js",
		"FastAPI",
		"Django",
		"Spring Boot",
		"REST API",
		"CI/CD",
		"Git",
	]

	_level_keywords = {
		"lead": ["lead", "tech lead", "team lead", "principal"],
		"senior": ["senior", "sr", "senior-level"],
		"mid": ["mid", "middle", "intermediate"],
		"junior": ["junior", "jr", "fresher"],
		"intern": ["intern", "internship", "thuc tap", "thực tập"],
	}

	_cert_keywords = [
		"AWS Certified",
		"Azure Fundamentals",
		"Google Cloud",
		"PMP",
		"Scrum Master",
		"IELTS",
		"TOEIC",
	]

	_locations = [
		"Ho Chi Minh",
		"HCM",
		"Hanoi",
		"Da Nang",
		"Can Tho",
		"Remote",
	]

	@staticmethod
	def _clean_text(value: str) -> str:
		return re.sub(r"\s+", " ", value or "").strip()

	@staticmethod
	def _clean_structured_text(value: str) -> str:
		if not value:
			return ""

		text = str(value).replace("\r\n", "\n").replace("\r", "\n")
		text = re.sub(r"[ \t]+", " ", text)
		lines: list[str] = []
		previous = ""
		for raw_line in text.splitlines():
			line = raw_line.strip()
			if not line:
				if lines and lines[-1] != "":
					lines.append("")
				continue
			if line == previous:
				continue
			lines.append(line)
			previous = line

		cleaned = "\n".join(lines).strip()
		return re.sub(r"\n{3,}", "\n\n", cleaned)

	@staticmethod
	def _strip_accents(value: str) -> str:
		normalized = unicodedata.normalize("NFD", value or "")
		return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")

	@staticmethod
	def _extract_text_from_pdf(file_bytes: bytes) -> str:
		doc = fitz.open(stream=file_bytes, filetype="pdf")
		try:
			pages = [page.get_text("text") for page in doc]
			return "\n".join(pages)
		finally:
			doc.close()

	@staticmethod
	def _extract_text_from_docx(file_bytes: bytes) -> str:
		try:
			with ZipFile(BytesIO(file_bytes)) as archive:
				xml_content = archive.read("word/document.xml")
		except (KeyError, BadZipFile) as exc:
			raise ValueError("Invalid DOCX file") from exc

		root = ET.fromstring(xml_content)
		texts: list[str] = []
		for node in root.iter():
			if node.tag.endswith("}t") and node.text:
				texts.append(node.text)
		return "\n".join(texts)

	@staticmethod
	def extract_text_from_file(filename: str, content_type: Optional[str], file_bytes: bytes) -> str:
		if not file_bytes:
			raise ValueError("Uploaded file is empty")

		if len(file_bytes) > 5 * 1024 * 1024:
			raise ValueError("File size exceeds 5MB limit")

		lower_name = (filename or "").lower()
		content = (content_type or "").lower()

		if lower_name.endswith(".pdf") or "pdf" in content:
			raw_text = CvIngestService._extract_text_from_pdf(file_bytes)
		elif lower_name.endswith(".docx") or "wordprocessingml" in content:
			raw_text = CvIngestService._extract_text_from_docx(file_bytes)
		elif lower_name.endswith((".txt", ".md", ".markdown")) or content.startswith("text/"):
			try:
				raw_text = file_bytes.decode("utf-8")
			except UnicodeDecodeError:
				raw_text = file_bytes.decode("latin-1", errors="ignore")
		else:
			raise ValueError("Unsupported file type. Use PDF, DOCX, TXT, or MD")

		cleaned = CvIngestService._clean_text(raw_text)
		if len(cleaned) < 30:
			raise ValueError("Could not extract enough text from CV file")
		return cleaned

	@staticmethod
	def extract_text_from_job_file(filename: str, content_type: Optional[str], file_bytes: bytes) -> str:
		lower_name = (filename or "").lower()
		content = (content_type or "").lower()
		if lower_name.endswith((".png", ".jpg", ".jpeg", ".webp")) or content.startswith("image/"):
			text = AIService.extract_text_from_image(file_bytes, content_type)
			cleaned = CvIngestService._clean_structured_text(text or "")
			if not cleaned or len(CvIngestService._clean_text(cleaned)) < 30:
				raise ValueError("Could not extract enough text from JD image. Check GEMINI_API_KEY or upload a clearer image")
			return cleaned

		if not file_bytes:
			raise ValueError("Uploaded file is empty")

		if len(file_bytes) > 5 * 1024 * 1024:
			raise ValueError("File size exceeds 5MB limit")

		if lower_name.endswith(".pdf") or "pdf" in content:
			raw_text = CvIngestService._extract_text_from_pdf(file_bytes)
		elif lower_name.endswith(".docx") or "wordprocessingml" in content:
			raw_text = CvIngestService._extract_text_from_docx(file_bytes)
		elif lower_name.endswith((".txt", ".md", ".markdown")) or content.startswith("text/"):
			try:
				raw_text = file_bytes.decode("utf-8")
			except UnicodeDecodeError:
				raw_text = file_bytes.decode("latin-1", errors="ignore")
		elif lower_name.endswith(".doc"):
			raise ValueError("Unsupported JD file type .doc. Use TXT, MD, PDF, or DOCX")
		else:
			raise ValueError("Unsupported file type. Use PDF, DOCX, TXT, MD, PNG, JPG, or WebP")

		cleaned = CvIngestService._clean_structured_text(raw_text)
		if len(CvIngestService._clean_text(cleaned)) < 30:
			raise ValueError("Could not extract enough text from JD file")
		return cleaned

	@staticmethod
	def _contains_keyword(text: str, keyword: str) -> bool:
		escaped = re.escape(keyword)
		pattern = rf"(^|[^a-zA-Z0-9+#.]){escaped}([^a-zA-Z0-9+#.]|$)"
		return re.search(pattern, text, flags=re.IGNORECASE) is not None

	@staticmethod
	def _normalize_level(raw_text: str) -> str:
		text = CvIngestService._strip_accents(raw_text or "").lower()
		keyword_map = {
			"intern": ["intern", "internship", "trainee", "thuc tap", "thuc tap sinh"],
			"lead": ["tech lead", "team lead", "lead", "principal"],
			"senior": ["senior", "sr", "expert"],
			"mid": ["middle", "mid level", "mid-level", "mid", "intermediate"],
			"junior": ["junior", "jr", "fresher", "entry level", "entry-level"],
		}
		for level in ["intern", "lead", "senior", "mid", "junior"]:
			keywords = keyword_map[level]
			if any(re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) for keyword in keywords):
				return level
		return "junior"

	@staticmethod
	def _extract_years_experience(raw_text: str) -> float:
		candidates = re.findall(
			r"(\d+(?:[\.,]\d+)?)\s*\+?\s*(?:years?|yrs?|năm)\s*(?:of\s+)?(?:experience|exp)?",
			raw_text,
			flags=re.IGNORECASE,
		)
		if not candidates:
			return 0.0
		values = [float(item.replace(",", ".")) for item in candidates]
		return max(values)

	@staticmethod
	def _infer_locations(raw_text: str) -> list[str]:
		found: list[str] = []
		for location in CvIngestService._locations:
			if location.lower() in (raw_text or "").lower() and location not in found:
				found.append(location)
		return found

	@staticmethod
	def _extract_certifications(raw_text: str) -> list[str]:
		found: list[str] = []
		for cert in CvIngestService._cert_keywords:
			if cert.lower() in (raw_text or "").lower():
				found.append(cert)
		return found

	@staticmethod
	def _default_cv_proficiency(name: str) -> float:
		advanced = {"Python", "Java", "SQL", "JavaScript", "TypeScript"}
		intermediate = {"Docker", "React", "Node.js", "FastAPI", "Django"}
		if name in advanced:
			return 0.75
		if name in intermediate:
			return 0.65
		return 0.55

	@staticmethod
	def _required_proficiency_from_importance(importance: float) -> float:
		if importance >= 0.8:
			return 0.8
		if importance >= 0.5:
			return 0.65
		return 0.5

	@staticmethod
	def _collect_skill_candidates(db: Session) -> list[str]:
		try:
			skill_names = [
				item.name
				for item in db.query(Skill).all()
				if item.name and not is_non_skill_role_label(item.name)
			]
		except SQLAlchemyError as exc:
			db.rollback()
			print(f"--- SKILL CANDIDATE DB FALLBACK: {exc} ---")
			return CvIngestService._fallback_skills

		if skill_names:
			return skill_names
		return CvIngestService._fallback_skills

	@staticmethod
	def _collect_skill_alias_lookup(db: Session, skill_candidates: list[str]) -> dict[str, str]:
		lookup: dict[str, str] = {}

		try:
			db_skills = db.query(Skill).all()
		except SQLAlchemyError as exc:
			db.rollback()
			print(f"--- SKILL ALIAS DB FALLBACK: {exc} ---")
			db_skills = []

		for skill in db_skills:
			if not skill.name:
				continue
			if is_non_skill_role_label(skill.name):
				continue

			canonical_name = skill.name
			keys = [skill.name, *(skill.aliases or []), *get_skill_aliases(skill.name)]
			for value in keys:
				key = normalize_skill_key(value)
				if key:
					lookup.setdefault(key, canonical_name)
				canonical_key = canonicalize_skill_key(value)
				if canonical_key:
					lookup.setdefault(canonical_key, canonical_name)

		if lookup:
			return lookup

		for name in skill_candidates:
			key = normalize_skill_key(name)
			if key:
				lookup.setdefault(key, name)
			canonical_key = canonicalize_skill_key(name)
			if canonical_key:
				lookup.setdefault(canonical_key, name)

			for alias in get_skill_aliases(name):
				alias_key = normalize_skill_key(alias)
				if alias_key:
					lookup.setdefault(alias_key, name)
				canonical_alias_key = canonicalize_skill_key(alias)
				if canonical_alias_key:
					lookup.setdefault(canonical_alias_key, name)

		return lookup

	@staticmethod
	def _merge_unique_strings(left: list[str], right: list[str]) -> list[str]:
		merged: list[str] = []
		seen: set[str] = set()
		for value in [*left, *right]:
			text = str(value or "").strip()
			if not text:
				continue
			key = text.lower()
			if key in seen:
				continue
			seen.add(key)
			merged.append(text)
		return merged

	@staticmethod
	def _normalize_level_value(value: str) -> str | None:
		candidate = str(value or "").strip().lower()
		if candidate in {"intern", "junior", "mid", "senior", "lead"}:
			return candidate
		return None

	@staticmethod
	def _merge_cv_skills(
		rule_skills: list[CvSkillInput],
		ai_payload: dict,
		skill_candidates: list[str],
		experience_years: float,
	) -> list[CvSkillInput]:
		candidate_map = {canonicalize_skill_key(name): name for name in skill_candidates}
		merged: dict[str, CvSkillInput] = {
			canonicalize_skill_key(skill.name): skill
			for skill in rule_skills
			if skill.name and skill.name.strip()
		}

		ai_skills = ai_payload.get("cv_skills", []) if isinstance(ai_payload, dict) else []
		if not isinstance(ai_skills, list):
			ai_skills = []

		for item in ai_skills:
			if not isinstance(item, dict):
				continue
			raw_name = str(item.get("name", "")).strip()
			if not raw_name:
				continue

			name = candidate_map.get(canonicalize_skill_key(raw_name), raw_name)
			if len(name) > 50:
				continue

			try:
				proficiency = float(item.get("proficiency", 0.55))
			except (TypeError, ValueError):
				proficiency = 0.55
			proficiency = max(0.0, min(1.0, proficiency))

			try:
				years_of_experience = float(item.get("years_of_experience", 0.0))
			except (TypeError, ValueError):
				years_of_experience = 0.0
			years_of_experience = max(0.0, min(experience_years if experience_years > 0 else 20.0, years_of_experience))

			key = canonicalize_skill_key(name)
			existing = merged.get(key)
			if existing:
				merged[key] = CvSkillInput(
					name=existing.name,
					proficiency=max(existing.proficiency, proficiency),
					years_of_experience=max(existing.years_of_experience, years_of_experience),
				)
			else:
				merged[key] = CvSkillInput(
					name=name,
					proficiency=proficiency,
					years_of_experience=years_of_experience,
				)

		result = list(merged.values())
		result.sort(key=lambda item: (item.proficiency, item.years_of_experience, item.name.lower()), reverse=True)
		return result

	@staticmethod
	def _job_level_from_title_and_level(job: Job) -> str:
		if job.level and getattr(job.level, "value", None):
			return job.level.value
		text = f"{job.title or ''} {job.description_raw or ''}".lower()
		return CvIngestService._normalize_level(text)

	@staticmethod
	def _extract_job_years_required(job: Job) -> float:
		text = f"{job.title or ''} {job.description_raw or ''}"
		return CvIngestService._extract_years_experience(text)

	@staticmethod
	def extract_profile(db: Session, cv_text: str) -> ExtractedCvProfile:
		cleaned_text = CvIngestService._clean_text(cv_text)
		skill_candidates = CvIngestService._collect_skill_candidates(db)
		skill_alias_lookup = CvIngestService._collect_skill_alias_lookup(db, skill_candidates)

		extracted_skills: list[CvSkillInput] = []
		seen: set[str] = set()
		for alias_key, candidate in skill_alias_lookup.items():
			candidate_key = canonicalize_skill_key(candidate)
			if not alias_key or not candidate_key or candidate_key in seen:
				continue
			if CvIngestService._contains_keyword(cleaned_text, alias_key):
				seen.add(candidate_key)
				extracted_skills.append(
					CvSkillInput(
						name=candidate,
						proficiency=CvIngestService._default_cv_proficiency(candidate),
						years_of_experience=0.0,
					)
				)

		experience_years = CvIngestService._extract_years_experience(cleaned_text)
		if experience_years > 0 and extracted_skills:
			years_per_skill = round(max(0.5, experience_years / max(1, len(extracted_skills) // 2)), 1)
			extracted_skills = [
				CvSkillInput(
					name=skill.name,
					proficiency=skill.proficiency,
					years_of_experience=years_per_skill,
				)
				for skill in extracted_skills
			]

		ai_profile = AIService.extract_cv_profile(cleaned_text, skill_candidates) or {}
		base_level = CvIngestService._normalize_level(cleaned_text)
		ai_level = CvIngestService._normalize_level_value(ai_profile.get("cv_level", ""))
		final_level = ai_level or base_level

		ai_years = ai_profile.get("cv_years_experience", 0)
		try:
			ai_years_value = float(ai_years)
		except (TypeError, ValueError):
			ai_years_value = 0.0
		final_years = max(experience_years, ai_years_value, 0.0)

		ai_locations_raw = ai_profile.get("preferred_locations", []) if isinstance(ai_profile, dict) else []
		ai_locations = [str(item).strip() for item in ai_locations_raw if str(item).strip()] if isinstance(ai_locations_raw, list) else []

		ai_certs_raw = ai_profile.get("cv_certifications", []) if isinstance(ai_profile, dict) else []
		ai_certs = [str(item).strip() for item in ai_certs_raw if str(item).strip()] if isinstance(ai_certs_raw, list) else []

		final_skills = CvIngestService._merge_cv_skills(
			rule_skills=extracted_skills,
			ai_payload=ai_profile,
			skill_candidates=skill_candidates,
			experience_years=final_years,
		)

		return ExtractedCvProfile(
			cv_level=final_level,
			cv_years_experience=round(final_years, 2),
			preferred_locations=CvIngestService._merge_unique_strings(
				CvIngestService._infer_locations(cleaned_text),
				ai_locations,
			),
			cv_certifications=CvIngestService._merge_unique_strings(
				CvIngestService._extract_certifications(cleaned_text),
				ai_certs,
			),
			cv_skills=final_skills,
		)

	@staticmethod
	def find_job(db: Session, job_id: Optional[int], job_url: Optional[str]) -> Job:
		query = db.query(Job)
		job: Optional[Job] = None
		if job_id:
			job = query.filter(Job.job_id == job_id).first()
		elif job_url:
			job = query.filter(Job.source_url == job_url).first()

		if not job:
			raise ValueError("Job not found. Provide a valid job_id or job_url")
		return job

	@staticmethod
	def build_job_context(job: Job) -> JobContext:
		job_skills: list[JobSkillInput] = []
		for item in job.job_skills:
			if not item.skill or not item.skill.name:
				continue
			if is_non_skill_role_label(item.skill.name):
				continue
			raw_importance = item.importance if item.importance is not None else 0.6
			importance = raw_importance if raw_importance <= 1 else min(raw_importance / 3.0, 1.0)
			job_skills.append(
				JobSkillInput(
					name=item.skill.name,
					importance=importance,
					required_proficiency=CvIngestService._required_proficiency_from_importance(importance),
				)
			)

		return JobContext(
			job_id=job.job_id,
			title=job.title,
			source_url=job.source_url,
			job_level=CvIngestService._job_level_from_title_and_level(job),
			job_years_required=CvIngestService._extract_job_years_required(job),
			job_location=job.location,
			job_is_remote=bool(job.location and "remote" in job.location.lower()),
			job_skills=job_skills,
		)

	@staticmethod
	def _infer_job_title(job_text: str, filename: str | None = None) -> str:
		lines = [line.strip() for line in re.split(r"[\r\n]+", job_text or "") if line.strip()]
		title_keywords = (
			"developer",
			"engineer",
			"intern",
			"fresher",
			"junior",
			"senior",
			"software",
			"data",
			"ai",
			"it",
			"thuc tap",
			"thuc tap sinh",
			"ky su",
			"lap trinh",
		)
		for line in lines[:10]:
			normalized = CvIngestService._strip_accents(line).lower()
			if any(keyword in normalized for keyword in title_keywords) and len(line) <= 160:
				return line.strip("() ")[:120]
		for line in lines[:8]:
			if len(line) <= 120 and not line.endswith(":"):
				return line
		if filename:
			stem = re.sub(r"\.[^.]+$", "", filename).replace("_", " ").replace("-", " ").strip()
			if stem:
				return stem[:120]
		return "Uploaded JD"

	@staticmethod
	def _infer_job_location(job_text: str) -> str | None:
		ascii_text = CvIngestService._strip_accents(job_text or "").lower()
		location_aliases = [
			("Remote", ["remote", "work from home", "lam viec tu xa"]),
			("Ho Chi Minh", ["ho chi minh", "hcm", "tp.hcm", "sai gon"]),
			("Ha Noi", ["ha noi", "hanoi"]),
			("Da Nang", ["da nang"]),
			("Can Tho", ["can tho"]),
		]
		for label, aliases in location_aliases:
			if any(alias in ascii_text for alias in aliases):
				return label
		return None

	@staticmethod
	def extract_uploaded_job_sections(job_text: str) -> dict[str, str | None]:
		lines = [line.strip(" \t•-") for line in (job_text or "").splitlines()]
		sections: dict[str, list[str]] = {
			"role_responsibilities": [],
			"skills_qualifications": [],
			"benefits": [],
		}
		current: str | None = None
		heading_map = {
			"role_responsibilities": [
				"role responsibilities",
				"role and responsibilities",
				"responsibilities",
				"job responsibilities",
				"mo ta cong viec",
				"trach nhiem",
			],
			"skills_qualifications": [
				"skills qualifications",
				"skills and qualifications",
				"qualifications",
				"requirements",
				"job requirements",
				"yeu cau",
				"ky nang",
			],
			"benefits": [
				"benefits",
				"salary and benefits",
				"quyen loi",
				"phuc loi",
				"dai ngo",
			],
		}

		for raw_line in lines:
			line = raw_line.strip()
			if not line:
				continue
			heading = CvIngestService._strip_accents(line).lower()
			heading = heading.replace("&", " and ")
			heading = re.sub(r"[^a-z0-9]+", " ", heading)
			heading = re.sub(r"\s+", " ", heading).strip()

			matched_section = None
			if len(heading) <= 60:
				for section, aliases in heading_map.items():
					if any(alias == heading or heading.startswith(alias) for alias in aliases):
						matched_section = section
						break
			if matched_section:
				current = matched_section
				continue

			if current:
				if re.fullmatch(r"trang\s+\d+|page\s+\d+", heading):
					continue
				sections[current].append(line)

		return {
			key: "\n".join(value).strip() or None
			for key, value in sections.items()
		}

	@staticmethod
	def _skill_has_text_evidence(job_text: str, skill_name: str) -> bool:
		if not skill_name:
			return False

		if CvIngestService._contains_keyword(job_text, skill_name):
			return True

		for alias in get_skill_aliases(skill_name):
			if CvIngestService._contains_keyword(job_text, alias):
				return True

		return False

	@staticmethod
	def build_uploaded_job_context(db: Session, job_text: str, filename: str | None = None) -> JobContext:
		cleaned_text = CvIngestService._clean_text(job_text)
		if len(cleaned_text) < 30:
			raise ValueError("JD text is too short")

		skill_candidates = CvIngestService._collect_skill_candidates(db)
		skill_alias_lookup = CvIngestService._collect_skill_alias_lookup(db, skill_candidates)
		candidate_map = {canonicalize_skill_key(name): name for name in skill_candidates}

		ai_job = AIService.extract_job_profile(cleaned_text, skill_candidates) or {}
		CvIngestService._last_uploaded_job_ai_payload = ai_job if isinstance(ai_job, dict) else {}
		ai_skills = ai_job.get("job_skills", []) if isinstance(ai_job, dict) else []
		if not isinstance(ai_skills, list):
			ai_skills = []

		job_skills: dict[str, JobSkillInput] = {}

		for item in ai_skills:
			if not isinstance(item, dict):
				continue
			raw_name = str(item.get("name", "")).strip()
			if not raw_name:
				continue

			for expanded_name in expand_skill_labels(raw_name):
				if is_non_skill_role_label(expanded_name):
					continue
				if not CvIngestService._skill_has_text_evidence(cleaned_text, expanded_name):
					continue
				key = canonicalize_skill_key(expanded_name)
				if not key:
					continue
				name = candidate_map.get(key, expanded_name)
				if len(name) > 50:
					continue

				try:
					importance = float(item.get("importance", 0.65))
				except (TypeError, ValueError):
					importance = 0.65
				importance = max(0.35, min(1.0, importance))

				try:
					required_proficiency = float(item.get("required_proficiency", 0))
				except (TypeError, ValueError):
					required_proficiency = 0.0
				if required_proficiency <= 0:
					required_proficiency = CvIngestService._required_proficiency_from_importance(importance)
				required_proficiency = max(0.0, min(1.0, required_proficiency))

				existing = job_skills.get(key)
				if existing and existing.importance >= importance and existing.required_proficiency >= required_proficiency:
					continue
				job_skills[key] = JobSkillInput(
					name=name,
					importance=importance,
					required_proficiency=required_proficiency,
				)

		if not job_skills:
			for alias_key, candidate in skill_alias_lookup.items():
				candidate_key = canonicalize_skill_key(candidate)
				if not alias_key or not candidate_key:
					continue
				if is_non_skill_role_label(candidate):
					continue
				if CvIngestService._contains_keyword(cleaned_text, alias_key):
					importance = 0.65
					job_skills.setdefault(
						candidate_key,
						JobSkillInput(
							name=candidate,
							importance=importance,
							required_proficiency=CvIngestService._required_proficiency_from_importance(importance),
						),
					)

		title = str(ai_job.get("title") or "").strip() if isinstance(ai_job, dict) else ""
		if not title:
			title = CvIngestService._infer_job_title(cleaned_text, filename)

		rule_level = CvIngestService._normalize_level(f"{title} {cleaned_text}")
		ai_level = CvIngestService._normalize_level_value(str(ai_job.get("job_level") or "")) if isinstance(ai_job, dict) else None
		level = rule_level or ai_level or "junior"

		try:
			ai_years = float(ai_job.get("job_years_required", 0)) if isinstance(ai_job, dict) else 0.0
		except (TypeError, ValueError):
			ai_years = 0.0

		location = str(ai_job.get("job_location") or "").strip() if isinstance(ai_job, dict) else ""
		if not location:
			location = CvIngestService._infer_job_location(cleaned_text) or ""

		is_remote = bool(ai_job.get("job_is_remote")) if isinstance(ai_job, dict) else False
		if not is_remote:
			is_remote = "remote" in f"{location} {cleaned_text}".lower()

		return JobContext(
			job_id=0,
			title=title[:255],
			source_url=f"uploaded://{filename or 'jd'}",
			job_level=level,
			job_years_required=round(max(CvIngestService._extract_years_experience(cleaned_text), ai_years, 0.0), 2),
			job_location=location or None,
			job_is_remote=is_remote,
			job_skills=sorted(job_skills.values(), key=lambda item: (item.importance, item.name.lower()), reverse=True),
		)
