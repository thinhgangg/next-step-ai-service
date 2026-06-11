"""add cv analysis history metadata

Revision ID: f6a2b8c9d1e4
Revises: e3f9a2c1b4d5
Create Date: 2026-06-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "f6a2b8c9d1e4"
down_revision: Union[str, None] = "e3f9a2c1b4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE cv_analysis_results ADD COLUMN IF NOT EXISTS user_id INTEGER")
    op.execute("ALTER TABLE cv_analysis_results ADD COLUMN IF NOT EXISTS cv_id INTEGER")
    op.execute("ALTER TABLE cv_analysis_results ADD COLUMN IF NOT EXISTS cv_filename VARCHAR(255)")
    op.execute("ALTER TABLE cv_analysis_results ADD COLUMN IF NOT EXISTS cv_text_excerpt TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cv_analysis_results_user_id ON cv_analysis_results (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cv_analysis_results_cv_id ON cv_analysis_results (cv_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cv_analysis_results_cv_id")
    op.execute("DROP INDEX IF EXISTS ix_cv_analysis_results_user_id")
    op.execute("ALTER TABLE cv_analysis_results DROP COLUMN IF EXISTS cv_text_excerpt")
    op.execute("ALTER TABLE cv_analysis_results DROP COLUMN IF EXISTS cv_filename")
    op.execute("ALTER TABLE cv_analysis_results DROP COLUMN IF EXISTS cv_id")
    op.execute("ALTER TABLE cv_analysis_results DROP COLUMN IF EXISTS user_id")
