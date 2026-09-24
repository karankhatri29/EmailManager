"""inbox intelligence: rules, settings, follow-ups, notifications, thread summaries, email metadata

Revision ID: 0003
Revises: 0002
"""

from email.utils import parseaddr

import sqlalchemy as sa

from alembic import op


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('ref', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_created_at'), 'notifications', ['created_at'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_table('rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('pattern', sa.String(length=320), nullable=False),
    sa.Column('category', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'kind', 'pattern')
    )
    op.create_index(op.f('ix_rules_user_id'), 'rules', ['user_id'], unique=False)
    op.create_table('thread_summaries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('thread_key', sa.String(length=160), nullable=False),
    sa.Column('message_count', sa.Integer(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'thread_key')
    )
    op.create_index(op.f('ix_thread_summaries_user_id'), 'thread_summaries', ['user_id'], unique=False)
    op.create_table('user_settings',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('timezone', sa.String(length=64), server_default='UTC', nullable=False),
    sa.Column('briefing_enabled', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('briefing_hour', sa.Integer(), server_default='8', nullable=False),
    sa.Column('briefing_last_sent', sa.Date(), nullable=True),
    sa.Column('urgent_alerts', sa.Boolean(), server_default='1', nullable=False),
    sa.Column('reminder_emails', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('followup_days', sa.Integer(), server_default='3', nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('follow_ups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('thread_id', sa.String(length=128), nullable=False),
    sa.Column('subject', sa.Text(), nullable=False),
    sa.Column('recipient', sa.String(length=512), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='waiting', nullable=False),
    sa.Column('nudge_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('nudged_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['mail_accounts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'thread_id')
    )
    op.create_index(op.f('ix_follow_ups_account_id'), 'follow_ups', ['account_id'], unique=False)
    op.create_index(op.f('ix_follow_ups_user_id'), 'follow_ups', ['user_id'], unique=False)
    op.add_column('activities', sa.Column('remind_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('activities', sa.Column('reminded_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_activities_remind_at'), 'activities', ['remind_at'], unique=False)
    op.add_column('emails', sa.Column('sender_address', sa.String(length=320), server_default='', nullable=False))
    op.add_column('emails', sa.Column('thread_id', sa.String(length=128), nullable=True))
    op.add_column('emails', sa.Column('reason', sa.Text(), nullable=True))
    op.add_column('emails', sa.Column('category_source', sa.String(length=16), server_default='auto', nullable=False))
    op.add_column('emails', sa.Column('is_done', sa.Boolean(), server_default='0', nullable=False))
    op.add_column('emails', sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emails', sa.Column('unsubscribe_url', sa.Text(), nullable=True))
    op.add_column('emails', sa.Column('unsubscribe_one_click', sa.Boolean(), server_default='0', nullable=False))
    op.add_column('emails', sa.Column('embedding', sa.LargeBinary(), nullable=True))
    op.create_index(op.f('ix_emails_sender_address'), 'emails', ['sender_address'], unique=False)
    op.create_index(op.f('ix_emails_thread_id'), 'emails', ['thread_id'], unique=False)

    # Existing rows: derive the bare sender address from the "Name <addr>" header.
    connection = op.get_bind()
    for email_id, sender in connection.execute(sa.text("SELECT id, sender FROM emails")).fetchall():
        address = parseaddr(sender or "")[1].strip().lower()
        if "@" in address:
            connection.execute(
                sa.text("UPDATE emails SET sender_address = :address WHERE id = :id"),
                {"address": address, "id": email_id},
            )


def downgrade() -> None:
    op.drop_index(op.f('ix_emails_thread_id'), table_name='emails')
    op.drop_index(op.f('ix_emails_sender_address'), table_name='emails')
    op.drop_column('emails', 'embedding')
    op.drop_column('emails', 'unsubscribe_one_click')
    op.drop_column('emails', 'unsubscribe_url')
    op.drop_column('emails', 'snoozed_until')
    op.drop_column('emails', 'is_done')
    op.drop_column('emails', 'category_source')
    op.drop_column('emails', 'reason')
    op.drop_column('emails', 'thread_id')
    op.drop_column('emails', 'sender_address')
    op.drop_index(op.f('ix_activities_remind_at'), table_name='activities')
    op.drop_column('activities', 'reminded_at')
    op.drop_column('activities', 'remind_at')
    op.drop_index(op.f('ix_follow_ups_user_id'), table_name='follow_ups')
    op.drop_index(op.f('ix_follow_ups_account_id'), table_name='follow_ups')
    op.drop_table('follow_ups')
    op.drop_table('user_settings')
    op.drop_index(op.f('ix_thread_summaries_user_id'), table_name='thread_summaries')
    op.drop_table('thread_summaries')
    op.drop_index(op.f('ix_rules_user_id'), table_name='rules')
    op.drop_table('rules')
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_created_at'), table_name='notifications')
    op.drop_table('notifications')
