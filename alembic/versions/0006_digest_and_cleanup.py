"""evening promo digest, unread state and automatic newsletter cleanup settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24 21:30:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('emails') as batch:
        batch.add_column(sa.Column('is_unread', sa.Boolean(), nullable=True))
    with op.batch_alter_table('user_settings') as batch:
        batch.add_column(sa.Column('digest_enabled', sa.Boolean(), server_default='0', nullable=False))
        batch.add_column(sa.Column('digest_hour', sa.Integer(), server_default='19', nullable=False))
        batch.add_column(sa.Column('digest_last_sent', sa.Date(), nullable=True))
        batch.add_column(sa.Column('auto_cleanup', sa.Boolean(), server_default='0', nullable=False))
        batch.add_column(sa.Column('cleanup_months', sa.Integer(), server_default='3', nullable=False))
        batch.add_column(sa.Column('cleanup_last_run', sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('user_settings') as batch:
        for column in ('cleanup_last_run', 'cleanup_months', 'auto_cleanup', 'digest_last_sent', 'digest_hour', 'digest_enabled'):
            batch.drop_column(column)
    with op.batch_alter_table('emails') as batch:
        batch.drop_column('is_unread')
