"""Persist the latest successful single Test result pointer.

Revision ID: 71b2c83d9e10
Revises: d4c92e1fa73b
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "71b2c83d9e10"
down_revision: Union[str, Sequence[str], None] = "d4c92e1fa73b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_latest_test",
        sa.Column("key", sa.String(length=16), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("research_latest_test")
