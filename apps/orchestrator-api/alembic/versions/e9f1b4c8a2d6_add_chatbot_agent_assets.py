"""add_chatbot_agent_assets

Per-agent reusable file library — floor plans, brochures, contract
templates. The bot autonomously sends a matching asset when a customer's
message keywords intersect the asset's keywords (Boss-OUT mode).

Revision ID: e9f1b4c8a2d6
Revises: d4e8a1b3c7f2
Create Date: 2026-05-14 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e9f1b4c8a2d6"
down_revision: Union[str, None] = "d4e8a1b3c7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chatbot_agent_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_url", sa.Text(), nullable=False),
        sa.Column("file_kind", sa.String(length=12), nullable=False, server_default="file"),
        sa.Column("file_mime", sa.String(length=80), nullable=True),
        sa.Column(
            "keywords_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("send_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_chatbot_agent_assets_agent_enabled",
        "chatbot_agent_assets",
        ["agent_id", "enabled"],
    )


def downgrade() -> None:
    op.drop_index("ix_chatbot_agent_assets_agent_enabled", table_name="chatbot_agent_assets")
    op.drop_table("chatbot_agent_assets")
