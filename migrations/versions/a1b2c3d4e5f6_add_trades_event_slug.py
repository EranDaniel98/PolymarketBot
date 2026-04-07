"""add trades.event_slug

Revision ID: a1b2c3d4e5f6
Revises: 6ed442800155
Create Date: 2026-04-07 14:30:00.000000

Adds the event_slug column to trades so the dashboard can render
'view on Polymarket' links pointing at https://polymarket.com/event/{slug}.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '6ed442800155'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add event_slug column to trades table."""
    op.add_column(
        'trades',
        sa.Column('event_slug', sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    """Remove event_slug column."""
    op.drop_column('trades', 'event_slug')
