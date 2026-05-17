"""expand job uploads columns

Revision ID: c2f8a7b1d9e3
Revises: b7c1d9e4a2f8
Create Date: 2026-05-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "c2f8a7b1d9e3"
down_revision: Union[str, None] = "b7c1d9e4a2f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS company_name VARCHAR(255)")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS level VARCHAR(50)")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS employment_type VARCHAR(50)")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS experience VARCHAR(50)")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS application_deadline DATE")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS location VARCHAR(255)")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS salary_min INTEGER")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS salary_max INTEGER")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS currency VARCHAR(10)")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS description_raw TEXT")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS description_clean TEXT")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS role_responsibilities TEXT")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS skills_qualifications TEXT")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS benefits TEXT")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS source_url VARCHAR(1000)")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS source_site VARCHAR(100)")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS posted_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE job_uploads ADD COLUMN IF NOT EXISTS status VARCHAR(50)")
    op.execute("UPDATE job_uploads SET description_raw = COALESCE(description_raw, content_excerpt, '')")
    op.execute("UPDATE job_uploads SET description_clean = COALESCE(description_clean, content_excerpt)")
    op.execute("UPDATE job_uploads SET level = COALESCE(level, job_level)")
    op.execute("UPDATE job_uploads SET location = COALESCE(location, job_location)")
    op.execute("UPDATE job_uploads SET source_site = COALESCE(source_site, 'upload')")
    op.execute("UPDATE job_uploads SET status = COALESCE(status, 'uploaded')")
    op.execute("ALTER TABLE job_uploads ALTER COLUMN description_raw SET NOT NULL")
    op.execute("ALTER TABLE job_uploads ALTER COLUMN source_site SET NOT NULL")
    op.execute("ALTER TABLE job_uploads ALTER COLUMN status SET NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS posted_at")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS source_site")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS source_url")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS benefits")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS skills_qualifications")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS role_responsibilities")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS description_clean")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS description_raw")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS salary_max")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS salary_min")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS location")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS application_deadline")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS experience")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS employment_type")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS level")
    op.execute("ALTER TABLE job_uploads DROP COLUMN IF EXISTS company_name")
