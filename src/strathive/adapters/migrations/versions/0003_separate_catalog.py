"""Keep only the securities referenced by ledger facts."""

from alembic import op
from sqlalchemy import delete, select, union

from strathive.adapters.schema import instruments, opening, trades

revision = "0003"
down_revision = "0002"


def upgrade():
    referenced = union(select(opening.c.symbol), select(trades.c.symbol))
    op.execute(delete(instruments).where(instruments.c.symbol.not_in(referenced)))
