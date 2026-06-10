from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from google import genai
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.job import Job
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.skill import Skill
from app.models.skill_course import SkillCourse


_SKILL_DURATION_FILE = Path(__file__).resolve().parents[1] / "data" / "skill_duration_baseline.json"


@dataclass(slots=True)
class KnowledgeChunkSpec:
    source_type: str
    source_id: str
    chunk_index: int
    content: str
    metadata_json: dict[str, Any]


class EmbeddingService:
    DEFAULT_MODEL = "gemini-embedding-001"
    EMBEDDING_API_VERSION = "v1beta"
    EMBEDDING_DIMENSION = 1536

    @classmethod
    def _ensure_embedding_table(cls, db: Session) -> None:
        db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS entity_embeddings (
                    embedding_id BIGSERIAL PRIMARY KEY,
                    entity_type VARCHAR(32) NOT NULL,
                    entity_id BIGINT NOT NULL,
                    embedding VECTOR(1536) NOT NULL,
                    embedding_model VARCHAR(100) NOT NULL,
                    embedding_dimension INTEGER NOT NULL DEFAULT 1536,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(entity_type, entity_id, embedding_model)
                )
                """
            )
        )
        db.commit()

    @classmethod
    def _fallback_embedding(cls, text_input: str) -> list[float]:
        seed_int = int(hashlib.sha256(text_input.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(seed_int)
        return [round(rng.uniform(-1, 1), 6) for _ in range(cls.EMBEDDING_DIMENSION)]

    @classmethod
    def embed_text(cls, text_input: str, model: Optional[str] = None) -> list[float]:
        if not text_input or not text_input.strip():
            return cls._fallback_embedding("empty")

        selected_model = model or cls.DEFAULT_MODEL
        try:
            client = genai.Client(
                api_key=settings.GEMINI_API_KEY,
                http_options={"api_version": cls.EMBEDDING_API_VERSION},
            )
            response = client.models.embed_content(
                model=selected_model,
                contents=text_input,
                config={"output_dimensionality": cls.EMBEDDING_DIMENSION},
            )

            values = response.embeddings[0].values if response and response.embeddings else None
            if not values:
                return cls._fallback_embedding(text_input)

            vector = [float(v) for v in values]
            if len(vector) > cls.EMBEDDING_DIMENSION:
                return vector[: cls.EMBEDDING_DIMENSION]
            if len(vector) < cls.EMBEDDING_DIMENSION:
                return vector + [0.0] * (cls.EMBEDDING_DIMENSION - len(vector))
            return vector
        except Exception:
            return cls._fallback_embedding(text_input)

    @classmethod
    def _build_job_text(cls, job: Job) -> str:
        skill_names = ", ".join(
            sorted(
                {
                    item.skill.name
                    for item in (job.job_skills or [])
                    if item.skill and item.skill.name
                }
            )
        )
        parts = [
            job.title or "",
            job.company.name if job.company else "",
            job.location or "",
            skill_names,
            job.description_clean or "",
            job.description_raw or "",
        ]
        return "\n".join(part.strip() for part in parts if part and part.strip())

    @classmethod
    def _build_skill_chunk_specs(cls, db: Session) -> list[KnowledgeChunkSpec]:
        specs: list[KnowledgeChunkSpec] = []
        skills = db.query(Skill).order_by(Skill.name.asc()).all()
        for skill in skills:
            aliases = list(skill.aliases or [])
            content = "\n".join(
                part
                for part in [
                    f"Skill: {skill.name}",
                    f"Category: {skill.category or ''}".strip(),
                    f"Aliases: {', '.join(aliases) if aliases else 'None'}",
                    f"Status: {'active' if skill.is_active else 'inactive'}",
                ]
                if part and part.strip()
            )
            specs.append(
                KnowledgeChunkSpec(
                    source_type="skill",
                    source_id=str(skill.skill_id),
                    chunk_index=0,
                    content=content,
                    metadata_json={
                        "name": skill.name,
                        "category": skill.category,
                        "aliases": aliases,
                        "is_active": skill.is_active,
                    },
                )
            )
        return specs

    @classmethod
    def _build_skill_course_chunk_specs(cls, db: Session) -> list[KnowledgeChunkSpec]:
        specs: list[KnowledgeChunkSpec] = []
        courses = (
            db.query(SkillCourse)
            .options(joinedload(SkillCourse.skill))
            .order_by(SkillCourse.id.asc())
            .all()
        )
        for course in courses:
            if not course.skill:
                continue
            content = "\n".join(
                part
                for part in [
                    f"Skill: {course.skill.name}",
                    f"Course: {course.title}",
                    f"Platform: {course.platform or ''}".strip(),
                    f"Level: {course.level or ''}".strip(),
                    f"Duration: {course.duration or ''}".strip(),
                    f"Duration hours: {course.duration_hours if course.duration_hours is not None else ''}",
                    f"URL: {course.url or ''}".strip(),
                ]
                if part and part.strip()
            )
            specs.append(
                KnowledgeChunkSpec(
                    source_type="skill_course",
                    source_id=str(course.id),
                    chunk_index=0,
                    content=content,
                    metadata_json={
                        "skill_name": course.skill.name,
                        "title": course.title,
                        "platform": course.platform,
                        "level": course.level,
                        "duration_hours": course.duration_hours,
                    },
                )
            )
        return specs

    @classmethod
    def _build_skill_duration_chunk_specs(cls) -> list[KnowledgeChunkSpec]:
        if not _SKILL_DURATION_FILE.exists():
            return []

        try:
            payload = json.loads(_SKILL_DURATION_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []

        skills = payload.get("skills") if isinstance(payload, dict) else []
        if not isinstance(skills, list):
            return []

        specs: list[KnowledgeChunkSpec] = []
        for item in skills:
            if not isinstance(item, dict):
                continue
            skill_name = str(item.get("skill") or "").strip()
            if not skill_name:
                continue
            aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
            baseline_hours = item.get("baseline_hours")
            reference_range = item.get("reference_range_hours")
            source_hint = str(item.get("source_hint") or "").strip()
            content = "\n".join(
                part
                for part in [
                    f"Skill: {skill_name}",
                    f"Aliases: {', '.join(str(alias).strip() for alias in aliases if str(alias).strip()) or 'None'}",
                    f"Baseline hours: {baseline_hours if baseline_hours is not None else ''}",
                    (
                        "Reference range hours: "
                        + (
                            f"{reference_range[0]}-{reference_range[1]}"
                            if isinstance(reference_range, list) and len(reference_range) >= 2
                            else ""
                        )
                    ).strip(),
                    f"Source hint: {source_hint}".strip(),
                ]
                if part and part.strip()
            )
            specs.append(
                KnowledgeChunkSpec(
                    source_type="skill_baseline",
                    source_id=skill_name.lower(),
                    chunk_index=0,
                    content=content,
                    metadata_json={
                        "skill": skill_name,
                        "aliases": aliases,
                        "baseline_hours": baseline_hours,
                        "reference_range_hours": reference_range,
                        "source_hint": source_hint,
                    },
                )
            )
        return specs

    @classmethod
    def build_knowledge_chunk_specs(cls, db: Session) -> list[KnowledgeChunkSpec]:
        specs: list[KnowledgeChunkSpec] = []
        specs.extend(cls._build_skill_chunk_specs(db))
        specs.extend(cls._build_skill_course_chunk_specs(db))
        specs.extend(cls._build_skill_duration_chunk_specs())
        return specs

    @classmethod
    def rebuild_knowledge_chunks(cls, db: Session, model: Optional[str] = None) -> dict[str, Any]:
        selected_model = model or cls.DEFAULT_MODEL
        specs = cls.build_knowledge_chunk_specs(db)

        db.query(KnowledgeChunk).delete(synchronize_session=False)
        db.flush()

        processed = 0
        for spec in specs:
            embedding_values = cls.embed_text(spec.content, selected_model)
            db.add(
                KnowledgeChunk(
                    source_type=spec.source_type,
                    source_id=spec.source_id,
                    chunk_index=spec.chunk_index,
                    content=spec.content,
                    metadata_json=spec.metadata_json,
                    embedding=embedding_values,
                    created_at=datetime.now(timezone.utc),
                )
            )
            processed += 1

        db.commit()
        return {
            "total_chunks": len(specs),
            "processed": processed,
            "failed": 0,
            "model": selected_model,
            "dimension": cls.EMBEDDING_DIMENSION,
        }

    @classmethod
    def retrieve_similar_chunks(
        cls,
        db: Session,
        query: str,
        top_k: int = 5,
        model: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        cleaned_query = (query or "").strip()
        if not cleaned_query:
            return []

        selected_model = model or cls.DEFAULT_MODEL
        query_embedding = cls.embed_text(cleaned_query, selected_model)
        query_literal = "[" + ",".join(str(value) for value in query_embedding) + "]"

        rows = db.execute(
            text(
                """
                SELECT
                    knowledge_chunk_id,
                    source_type,
                    source_id,
                    chunk_index,
                    content,
                    metadata,
                    (embedding <=> CAST(:query_embedding AS vector)) AS distance
                FROM knowledge_chunks
                ORDER BY embedding <=> CAST(:query_embedding AS vector)
                LIMIT :top_k
                """
            ),
            {"query_embedding": query_literal, "top_k": max(1, top_k)},
        ).mappings().all()

        results: list[dict[str, Any]] = []
        for row in rows:
            distance = float(row["distance"] or 0.0)
            similarity = max(0.0, 1.0 - distance)
            results.append(
                {
                    "knowledge_chunk_id": row["knowledge_chunk_id"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "chunk_index": row["chunk_index"],
                    "content": row["content"],
                    "metadata": row["metadata"] or {},
                    "distance": round(distance, 6),
                    "similarity": round(similarity, 6),
                }
            )

        return results

    @classmethod
    def sync_job_embeddings(
        cls,
        db: Session,
        limit: int = 20,
        only_missing: bool = True,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        cls._ensure_embedding_table(db)

        selected_model = model or cls.DEFAULT_MODEL
        jobs = db.query(Job).order_by(Job.scraped_at.desc()).limit(max(limit, 1)).all()

        processed = 0
        skipped = 0
        failed = 0

        for job in jobs:
            try:
                if only_missing:
                    exists = db.execute(
                        text(
                            """
                            SELECT 1
                            FROM entity_embeddings
                            WHERE entity_type = 'job'
                              AND entity_id = :entity_id
                              AND embedding_model = :embedding_model
                            LIMIT 1
                            """
                        ),
                        {"entity_id": job.job_id, "embedding_model": selected_model},
                    ).scalar()
                    if exists:
                        skipped += 1
                        continue

                source_text = cls._build_job_text(job)
                embedding_values = cls.embed_text(source_text, selected_model)
                embedding_literal = "[" + ",".join(str(v) for v in embedding_values) + "]"

                db.execute(
                    text(
                        """
                        INSERT INTO entity_embeddings (
                            entity_type,
                            entity_id,
                            embedding,
                            embedding_model,
                            embedding_dimension,
                            created_at
                        ) VALUES (
                            'job',
                            :entity_id,
                            CAST(:embedding AS vector),
                            :embedding_model,
                            :embedding_dimension,
                            :created_at
                        )
                        ON CONFLICT (entity_type, entity_id, embedding_model)
                        DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            embedding_dimension = EXCLUDED.embedding_dimension,
                            created_at = EXCLUDED.created_at
                        """
                    ),
                    {
                        "entity_id": job.job_id,
                        "embedding": embedding_literal,
                        "embedding_model": selected_model,
                        "embedding_dimension": cls.EMBEDDING_DIMENSION,
                        "created_at": datetime.now(timezone.utc),
                    },
                )
                processed += 1
            except Exception:
                db.rollback()
                failed += 1
                continue

        db.commit()
        return {
            "total_jobs": len(jobs),
            "processed": processed,
            "skipped": skipped,
            "failed": failed,
            "model": selected_model,
            "dimension": cls.EMBEDDING_DIMENSION,
        }
