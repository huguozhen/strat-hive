"""Separate ETF commissions while preserving each account's existing rate."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"


def upgrade():
    op.add_column("account", sa.Column("etf_commission_rate", sa.String(), nullable=False,
                                      server_default="0"))
    account = sa.table("account", sa.column("commission_rate"), sa.column("etf_commission_rate"))
    op.execute(account.update().values(etf_commission_rate=account.c.commission_rate))
