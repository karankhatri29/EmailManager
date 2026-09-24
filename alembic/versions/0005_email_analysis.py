"""store the date / deadline / action each email was analysed to

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-24 20:10:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("emails") as batch:
        batch.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("due_kind", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("due_text", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("due_time", sa.Time(), nullable=True))
        batch.add_column(sa.Column("due_confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("nlp_version", sa.Integer(), server_default="0", nullable=False))
        batch.create_index("ix_emails_due_date", ["due_date"])


def downgrade() -> None:
    with op.batch_alter_table("emails") as batch:
        batch.drop_index("ix_emails_due_date")
        for column in ("nlp_version", "due_confidence", "due_time", "due_text", "due_kind", "due_date"):
            batch.drop_column(column)
