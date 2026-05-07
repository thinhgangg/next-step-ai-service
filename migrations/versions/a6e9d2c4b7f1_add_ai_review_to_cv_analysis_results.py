"""add ai review to cv analysis results

Revision ID: a6e9d2c4b7f1
Revises: f1b2c3d4e5f6
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6e9d2c4b7f1"
down_revision: Union[str, None] = "f1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cv_analysis_results", sa.Column("ai_review_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("cv_analysis_results", "ai_review_json")
