"""multi-user: users, mail accounts, per-account emails and sync state, activities

The old single-user `emails` and `sync_state` tables only cached data that is re-synced from the
mailbox, so they are dropped and recreated with ownership columns instead of being migrated.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_emails_date", table_name="emails")
    op.drop_index("ix_emails_category", table_name="emails")
    op.drop_table("emails")
    op.drop_table("sync_state")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("calendar_token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_calendar_token", "users", ["calendar_token"], unique=True)

    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("email_address", sa.String(length=320), nullable=False),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "email_address"),
    )
    op.create_index("ix_mail_accounts_user_id", "mail_accounts", ["user_id"])

    op.create_table(
        "emails",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("sender", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("task", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["mail_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_emails_user_id", "emails", ["user_id"])
    op.create_index("ix_emails_account_id", "emails", ["account_id"])
    op.create_index("ix_emails_category", "emails", ["category"])
    op.create_index("ix_emails_date", "emails", ["date"])

    op.create_table(
        "sync_state",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=32), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["mail_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "timeframe"),
    )

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["email_id"], ["emails.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_id"),
    )
    op.create_index("ix_activities_user_id", "activities", ["user_id"])
    op.create_index("ix_activities_start_at", "activities", ["start_at"])


def downgrade() -> None:
    op.drop_table("activities")
    op.drop_table("sync_state")
    op.drop_table("emails")
    op.drop_table("mail_accounts")
    op.drop_table("users")

    op.create_table(
        "emails",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("sender", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("task", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_emails_category", "emails", ["category"])
    op.create_index("ix_emails_date", "emails", ["date"])
    op.create_table(
        "sync_state",
        sa.Column("timeframe", sa.String(length=32), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("timeframe"),
    )
