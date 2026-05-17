from __future__ import annotations

import json
import re
from typing import Any

from google import genai
from google.genai import types

from app.core.config import settings


class AIService:
	MODEL_NAME = "gemini-2.5-flash"

	@staticmethod
	def _build_client() -> genai.Client:
		return genai.Client(
			api_key=settings.GEMINI_API_KEY,
			http_options={"api_version": "v1"},
		)

	@staticmethod
	def _extract_json(text: str) -> dict[str, Any] | None:
		if not text:
			return None

		normalized = text.strip()
		try:
			loaded = json.loads(normalized)
			if isinstance(loaded, dict):
				return loaded
		except json.JSONDecodeError:
			pass

		match = re.search(r"\{[\s\S]*\}", normalized)
		if not match:
			return None

		try:
			loaded = json.loads(match.group(0))
			if isinstance(loaded, dict):
				return loaded
		except json.JSONDecodeError:
			return None
		return None

	@staticmethod
	def extract_skills(jd_text: str) -> str:
		if not jd_text or len(jd_text.strip()) < 50:
			return "Mô tả công việc quá ngắn."

		try:
			client = AIService._build_client()
			prompt = f"Trích xuất danh sách kỹ năng IT từ văn bản sau (chỉ trả về từ khóa, cách nhau bằng dấu phẩy): {jd_text}"
			response = client.models.generate_content(
				model=AIService.MODEL_NAME,
				contents=prompt,
			)
			if response and response.text:
				return response.text.strip()
			return "AI không trả về kỹ năng."
		except Exception as exc:
			print(f"--- THÔNG BÁO LỖI: {str(exc)} ---")
			return f"Lỗi AI: {str(exc)}"

	@staticmethod
	def extract_cv_profile(cv_text: str, skill_candidates: list[str]) -> dict[str, Any] | None:
		if not settings.CV_AI_ENRICHMENT_ENABLED:
			return None

		if not settings.GEMINI_API_KEY:
			return None

		cleaned_text = (cv_text or "").strip()
		if len(cleaned_text) < 80:
			return None

		limited_text = cleaned_text[:8000]
		candidate_text = ", ".join(skill_candidates[:400])

		prompt = (
			"Bạn là chuyên gia phân tích CV IT. "
			"Hãy trích xuất hồ sơ ứng viên và chỉ trả về JSON hợp lệ, không thêm markdown. "
			"Schema JSON:\n"
			"{\n"
			'  "cv_level": "intern|junior|mid|senior|lead",\n'
			'  "cv_years_experience": number,\n'
			'  "preferred_locations": [string],\n'
			'  "cv_certifications": [string],\n'
			'  "cv_skills": [\n'
			'    {"name": string, "proficiency": number(0..1), "years_of_experience": number >= 0}\n'
			"  ]\n"
			"}\n"
			"Yêu cầu:\n"
			"- Chỉ chọn skill trong danh sách ứng viên nếu có thể.\n"
			"- Không bịa thông tin; nếu không rõ thì để giá trị bảo thủ (0 hoặc mảng rỗng).\n"
			"- Dùng tiếng Anh cho tên skill nếu có thể.\n\n"
			f"Danh sách skill ứng viên: {candidate_text}\n\n"
			f"CV text:\n{limited_text}"
		)

		try:
			client = AIService._build_client()
			response = client.models.generate_content(
				model=AIService.MODEL_NAME,
				contents=prompt,
			)
			text = (response.text or "").strip() if response else ""
			if not text:
				return None

			data = AIService._extract_json(text)
			if not data:
				return None
			return data
		except Exception as exc:
			print(f"--- CV AI ENRICHMENT ERROR: {exc} ---")
			return None

	@staticmethod
	def extract_text_from_image(file_bytes: bytes, content_type: str | None = None) -> str | None:
		if not settings.CV_AI_ENRICHMENT_ENABLED:
			return None

		if not settings.GEMINI_API_KEY:
			return None

		if not file_bytes:
			return None

		mime_type = (content_type or "image/png").strip() or "image/png"
		prompt = (
			"Bạn là OCR cho ảnh job description. "
			"Hãy đọc toàn bộ chữ trong ảnh và trả về văn bản thuần, giữ thứ tự nội dung càng sát ảnh càng tốt. "
			"Không nhận xét, không thêm markdown."
		)

		try:
			client = AIService._build_client()
			response = client.models.generate_content(
				model=AIService.MODEL_NAME,
				contents=[
					prompt,
					types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
				],
			)
			text = (response.text or "").strip() if response else ""
			return text or None
		except Exception as exc:
			print(f"--- IMAGE OCR AI ERROR: {exc} ---")
			return None

	@staticmethod
	def extract_job_profile(job_text: str, skill_candidates: list[str]) -> dict[str, Any] | None:
		if not settings.CV_AI_ENRICHMENT_ENABLED:
			return None

		if not settings.GEMINI_API_KEY:
			return None

		cleaned_text = (job_text or "").strip()
		if len(cleaned_text) < 80:
			return None

		limited_text = cleaned_text[:8000]
		candidate_text = ", ".join(skill_candidates[:400])

		prompt = (
			"You are parsing an uploaded job description only. "
			"Do not use resume/CV information, candidate information, or assumptions. "
			"Return valid JSON only, with no markdown and no text outside JSON.\n"
			"JSON schema:\n"
			"{\n"
			'  "title": string,\n'
			'  "company_name": string,\n'
			'  "job_level": "intern|junior|mid|senior|lead",\n'
			'  "employment_type": string,\n'
			'  "experience": string,\n'
			'  "job_years_required": number,\n'
			'  "job_location": string,\n'
			'  "job_is_remote": boolean,\n'
			'  "salary_min": number|null,\n'
			'  "salary_max": number|null,\n'
			'  "currency": "VND|USD|null",\n'
			'  "application_deadline": "YYYY-MM-DD|null",\n'
			'  "role_responsibilities": string,\n'
			'  "skills_qualifications": string,\n'
			'  "benefits": string,\n'
			'  "job_skills": [\n'
			'    {"name": string, "importance": number(0..1), "required_proficiency": number(0..1)}\n'
			"  ]\n"
			"}\n"
			"Rules:\n"
			"- Extract only fields explicitly present in the JD text. Use empty string or null when unclear.\n"
			"- Do not copy any resume/CV skill, education, project, candidate name, or candidate experience.\n"
			"- company_name must be the hiring company only, not a person name.\n"
			"- title must be the job title only, not a page title or long sentence.\n"
			"- experience must be short, for example '2+ years'. If there is no explicit year requirement, use ''.\n"
			"- employment_type must be one short label: Fulltime, Part-time, Contract, Internship, Remote, Hybrid, Freelance, or ''.\n"
			"- salary_min/salary_max must be numeric only. Convert hourly/monthly values only if shown directly in the JD; otherwise null.\n"
			"- role_responsibilities, skills_qualifications, benefits must be copied/summarized from matching JD sections only.\n"
			"- job_skills must include real skills required by the JD. Do not include role labels, generic soft skills, company tools, or unrelated words.\n"
			"- Prefer names from this allowed skill taxonomy when possible. If a required skill is outside the taxonomy but clearly present, include it.\n"
			"- importance is high for must-have/required skills, medium for normal requirements, low for nice-to-have.\n"
			"- required_proficiency: basic 0.5, intermediate 0.65, strong/expert 0.8.\n\n"
			f"Allowed skill taxonomy: {candidate_text}\n\n"
			f"Uploaded JD text:\n{limited_text}"
		)

		try:
			client = AIService._build_client()
			response = client.models.generate_content(
				model=AIService.MODEL_NAME,
				contents=prompt,
			)
			text = (response.text or "").strip() if response else ""
			if not text:
				return None

			data = AIService._extract_json(text)
			if not data:
				return None
			return data
		except Exception as exc:
			print(f"--- JOB AI ENRICHMENT ERROR: {exc} ---")
			return None

	@staticmethod
	def generate_cv_job_review(
		cv_text: str,
		extracted_profile: dict[str, Any],
		job_context: dict[str, Any],
		job_match: dict[str, Any],
		gap_analysis: dict[str, Any],
		roadmap: dict[str, Any],
	) -> dict[str, Any] | None:
		if not settings.CV_AI_ENRICHMENT_ENABLED:
			return None

		if not settings.GEMINI_API_KEY:
			return None

		limited_cv = (cv_text or "").strip()[:5000]
		payload = {
			"extracted_profile": extracted_profile,
			"job_context": job_context,
			"job_match": job_match,
			"gap_analysis": gap_analysis,
			"roadmap": roadmap,
		}

		prompt = (
			"Bạn là chuyên gia tuyển dụng IT và cố vấn phát triển nghề nghiệp. "
			"Hãy nhận xét mức độ phù hợp của CV với job dựa trên dữ liệu đã tính toán sẵn. "
			"Chỉ trả về JSON hợp lệ, không markdown, không thêm văn bản ngoài JSON.\n"
			"Schema JSON:\n"
			"{\n"
			'  "summary": string,\n'
			'  "strengths": [string],\n'
			'  "concerns": [string],\n'
			'  "recommendations": [string],\n'
			'  "verdict": "strong_match|potential_match|weak_match"\n'
			"}\n"
			"Yêu cầu:\n"
			"- Viết tiếng Việt, ngắn gọn, thực tế, không tâng bốc quá mức.\n"
			"- Không bịa kỹ năng hoặc kinh nghiệm ngoài dữ liệu.\n"
			"- Nhận xét nên liên hệ score, missing/weak skills, roadmap và thời gian học.\n"
			"- strengths, concerns, recommendations mỗi danh sách tối đa 4 ý.\n\n"
			f"Dữ liệu phân tích:\n{json.dumps(payload, ensure_ascii=False)[:12000]}\n\n"
			f"CV excerpt:\n{limited_cv}"
		)

		try:
			client = AIService._build_client()
			response = client.models.generate_content(
				model=AIService.MODEL_NAME,
				contents=prompt,
			)
			text = (response.text or "").strip() if response else ""
			data = AIService._extract_json(text)
			if not data:
				return None

			def clean_list(value: Any) -> list[str]:
				if not isinstance(value, list):
					return []
				return [str(item).strip() for item in value[:4] if str(item).strip()]

			verdict = str(data.get("verdict") or "").strip()
			if verdict not in {"strong_match", "potential_match", "weak_match"}:
				verdict = "potential_match"

			return {
				"summary": str(data.get("summary") or "").strip()[:800],
				"strengths": clean_list(data.get("strengths")),
				"concerns": clean_list(data.get("concerns")),
				"recommendations": clean_list(data.get("recommendations")),
				"verdict": verdict,
				"source": "ai",
			}
		except Exception as exc:
			print(f"--- CV JOB AI REVIEW ERROR: {exc} ---")
			return None
