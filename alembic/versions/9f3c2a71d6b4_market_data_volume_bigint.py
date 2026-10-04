"""Widen market_data.volume to BIGINT.

Revision ID: 9f3c2a71d6b4
Revises: d4c92e1fa73b
Create Date: 2026-10-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f3c2a71d6b4"
down_revision: Union[str, Sequence[str], None] = "d4c92e1fa73b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "market_data",
        "volume",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # PostgreSQL rejects the narrowing cast if any stored value exceeds the
    # INTEGER range, leaving the BIGINT column and its data intact.
    op.alter_column(
        "market_data",
        "volume",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
