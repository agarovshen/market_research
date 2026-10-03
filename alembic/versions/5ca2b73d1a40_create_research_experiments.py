"""Create durable research experiment results.

Revision ID: 5ca2b73d1a40
Revises: e7dcd57c08ef
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5ca2b73d1a40"
down_revision: Union[str, Sequence[str], None] = "e7dcd57c08ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_experiments",
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=200), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("timeframe", sa.String(length=5), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("experiment_id"),
    )
    op.create_index("ix_research_experiments_strategy_id", "research_experiments", ["strategy_id"])
    op.create_index("ix_research_experiments_symbol", "research_experiments", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_research_experiments_symbol", table_name="research_experiments")
    op.drop_index("ix_research_experiments_strategy_id", table_name="research_experiments")
    op.drop_table("research_experiments")
