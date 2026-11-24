"""Keep automatically selected boundary price provenance with ledger facts."""
from alembic import op
import sqlalchemy as sa
revision = '0005'
down_revision = '0004'


def upgrade():
    op.add_column('opening_position', sa.Column('valuation_meta', sa.JSON(), nullable=True))
