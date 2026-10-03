"""Prevent duplicate market data

Revision ID: e7dcd57c08ef
Revises: f40c9c792f49
Create Date: 2026-10-02 14:27:14.683596

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7dcd57c08ef'
down_revision: Union[str, Sequence[str], None] = 'f40c9c792f49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_market_data_instrument_timestamp",
        "market_data",
        ["instrument_id", "timestamp"],
    )

def downgrade() -> None:
    op.drop_constraint(
        "uq_market_data_instrument_timestamp",
        "market_data",
        type_="unique",
    )