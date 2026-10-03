"""Persist deterministic advanced research outputs.

Revision ID: d4c92e1fa73b
Revises: 5ca2b73d1a40
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4c92e1fa73b"
down_revision: Union[str, Sequence[str], None] = "5ca2b73d1a40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_analyses",
        sa.Column("analysis_id", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("analysis_id"),
    )
    op.create_index("ix_research_analyses_method", "research_analyses", ["method"])


def downgrade() -> None:
    op.drop_index("ix_research_analyses_method", table_name="research_analyses")
    op.drop_table("research_analyses")
