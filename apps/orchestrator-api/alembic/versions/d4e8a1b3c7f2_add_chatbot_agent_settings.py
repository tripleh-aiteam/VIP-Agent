"""add_chatbot_agent_settings

Per-agent runtime settings table. Currently holds the Boss-IN/Boss-OUT
manual mode override + reason + expiry. Survives orchestrator restarts
so the boss's manual flip doesn't silently revert to auto-detect.

Revision ID: d4e8a1b3c7f2
Revises: c2e8a3f1d5b9
Create Date: 2026-05-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d4e8a1b3c7f2"
down_revision: Union[str, None] = "c2e8a3f1d5b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chatbot_agent_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, unique=True, index=True),
        sa.Column("mode_override", sa.String(length=8), nullable=True),
        sa.Column("mode_reason", sa.String(length=40), nullable=True),
        sa.Column("mode_reason_note", sa.Text(), nullable=True),
        sa.Column("mode_expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "auto_mode_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("chatbot_agent_settings")
