"""Owner-scoped maintenance requests and service history.

Revision ID: e7b01486cf20
Revises: a4d68bedf731
"""
from alembic import op
import sqlalchemy as sa

revision = 'e7b01486cf20'
down_revision = 'a4d68bedf731'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('service_request',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('kiosk_id', sa.Integer(), sa.ForeignKey('kiosk.id'), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('requester_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('request_key', sa.String(32), nullable=False),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('description', sa.String(2000), nullable=False),
        sa.Column('category', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('requester_id', 'request_key', name='uq_service_request_retry'),
        sa.CheckConstraint("status IN ('open','in_progress','resolved')", name='ck_service_request_status'),
        sa.CheckConstraint("category IN ('printer','paper_ink','connectivity','other')", name='ck_service_request_category'),
        sa.CheckConstraint('version > 0', name='ck_service_request_version'))
    for name in ('kiosk_id', 'owner_id', 'status'):
        op.create_index('ix_service_request_'+name, 'service_request', [name])
    op.create_table('service_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('request_id', sa.String(32), sa.ForeignKey('service_request.id'), nullable=False),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('note', sa.String(2000), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False))
    op.create_index('ix_service_event_request_id', 'service_event', ['request_id'])


def downgrade():
    # Destructive rollback: requires backup/review; never part of staging setup.
    op.drop_table('service_event')
    op.drop_table('service_request')
