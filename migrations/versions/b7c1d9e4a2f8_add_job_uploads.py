"""add job uploads

Revision ID: b7c1d9e4a2f8
Revises: a6e9d2c4b7f1
Create Date: 2026-05-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c1d9e4a2f8"
down_revision: Union[str, None] = "a6e9d2c4b7f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS job_uploads (
            job_upload_id SERIAL PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            source_filename VARCHAR(1000),
            content_excerpt TEXT,
            job_context_json JSON NOT NULL,
            job_level VARCHAR(50),
            job_years_required FLOAT,
            job_location VARCHAR(255),
            job_is_remote BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_uploads_job_upload_id ON job_uploads (job_upload_id)")

    op.execute("ALTER TABLE cv_analysis_results ADD COLUMN IF NOT EXISTS job_upload_id INTEGER")
    op.execute("ALTER TABLE cv_analysis_results ALTER COLUMN job_job_id DROP NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cv_analysis_results_job_upload_id "
        "ON cv_analysis_results (job_upload_id)"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_cv_analysis_results_job_upload_id_job_uploads'
            ) THEN
                ALTER TABLE cv_analysis_results
                ADD CONSTRAINT fk_cv_analysis_results_job_upload_id_job_uploads
                FOREIGN KEY (job_upload_id) REFERENCES job_uploads(job_upload_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_cv_analysis_results_job_upload_id_job_uploads",
        "cv_analysis_results",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_cv_analysis_results_job_upload_id"), table_name="cv_analysis_results")
    op.drop_column("cv_analysis_results", "job_upload_id")
    op.alter_column("cv_analysis_results", "job_job_id", existing_type=sa.Integer(), nullable=False)

    op.drop_index(op.f("ix_job_uploads_job_upload_id"), table_name="job_uploads")
    op.drop_table("job_uploads")
