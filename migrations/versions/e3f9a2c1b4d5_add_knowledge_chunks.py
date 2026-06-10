"""add knowledge chunks

Revision ID: e3f9a2c1b4d5
Revises: c2f8a7b1d9e3
Create Date: 2026-06-10 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3f9a2c1b4d5"
down_revision: Union[str, None] = "c2f8a7b1d9e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            knowledge_chunk_id BIGSERIAL PRIMARY KEY,
            source_type VARCHAR(50) NOT NULL,
            source_id VARCHAR(64) NOT NULL,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL,
            metadata JSON NOT NULL DEFAULT '{}'::json,
            embedding VECTOR(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_knowledge_chunks_source UNIQUE (source_type, source_id, chunk_index)
        )
        """
    )
    op.create_index(
        op.f("ix_knowledge_chunks_knowledge_chunk_id"),
        "knowledge_chunks",
        ["knowledge_chunk_id"],
        unique=False,
    )
    op.create_index(op.f("ix_knowledge_chunks_source_type"), "knowledge_chunks", ["source_type"], unique=False)
    op.create_index(op.f("ix_knowledge_chunks_source_id"), "knowledge_chunks", ["source_id"], unique=False)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_cosine
        ON knowledge_chunks
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_cosine")
    op.drop_index(op.f("ix_knowledge_chunks_source_id"), table_name="knowledge_chunks")
    op.drop_index(op.f("ix_knowledge_chunks_source_type"), table_name="knowledge_chunks")
    op.drop_index(op.f("ix_knowledge_chunks_knowledge_chunk_id"), table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
