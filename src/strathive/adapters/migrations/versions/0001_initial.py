"""Initial ledger schema."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('account',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('commission_rate', sa.String(), nullable=False),
    sa.Column('opened_at', sa.String(), nullable=False),
    sa.Column('initial_cash', sa.Integer(), nullable=False),
    sa.Column('current', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('instrument',
    sa.Column('symbol', sa.String(), nullable=False),
    sa.Column('code', sa.String(), nullable=False),
    sa.Column('market', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('full_name', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('symbol')
    )
    op.create_table('strategy',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('pool',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('initial_cash', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('ledger_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('pool_id', sa.Integer(), nullable=True),
    sa.Column('effective_at', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ),
    sa.ForeignKeyConstraint(['pool_id'], ['pool.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sqlite_autoincrement=True
    )
    op.create_index(op.f('ix_ledger_event_account_id'), 'ledger_event', ['account_id'], unique=False)
    op.create_table('opening_position',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('pool_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_cost', sa.String(), nullable=False),
    sa.Column('position_date', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['pool_id'], ['pool.id'], ),
    sa.ForeignKeyConstraint(['symbol'], ['instrument.symbol'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pool_id', 'symbol')
    )
    op.create_table('run',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_id', sa.Integer(), nullable=False),
    sa.Column('pool_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['pool_id'], ['pool.id'], ),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategy.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pool_id')
    )
    op.create_table('trade',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(), nullable=False),
    sa.Column('side', sa.String(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('price', sa.String(), nullable=False),
    sa.Column('commission_rate', sa.String(), nullable=False),
    sa.Column('calculated', sa.JSON(), nullable=False),
    sa.Column('fees', sa.JSON(), nullable=False),
    sa.Column('overrides', sa.JSON(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('note', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['id'], ['ledger_event.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], ),
    sa.ForeignKeyConstraint(['symbol'], ['instrument.symbol'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('trade')
    op.drop_table('run')
    op.drop_table('opening_position')
    op.drop_index(op.f('ix_ledger_event_account_id'), table_name='ledger_event')
    op.drop_table('ledger_event')
    op.drop_table('pool')
    op.drop_table('strategy')
    op.drop_table('instrument')
    op.drop_table('account')
