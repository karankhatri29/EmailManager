"""widen columns that now hold encrypted text

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24 23:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Ciphertext is about 1.4x the text plus roughly 100 bytes, so short String(N) columns would overflow.
CHANGES = (
    ("emails", "task", sa.String(length=255), True),
    ("emails", "due_text", sa.String(length=80), True),
    ("notifications", "title", sa.String(length=255), False),
)


def upgrade() -> None:
    for table, column, old, nullable in CHANGES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=old, type_=sa.Text(), existing_nullable=nullable)


def downgrade() -> None:
    for table, column, old, nullable in CHANGES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.Text(), type_=old, existing_nullable=nullable)
