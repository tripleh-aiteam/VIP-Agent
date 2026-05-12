"""add_voice_calling_tables

Voice / Calling Agent — v1.2.0 schema. Six tables for the multi-tenant
voice surface: provider mapping, calls, transcript turns, recordings,
batch campaigns, batch recipients. Every domain row carries `agent_id`
for tenant isolation; the dashboard's REST endpoints scope queries by
the URL's {agent_id} path segment.

Revision ID: b1c4f5a2d3e0
Revises: aca8a5fb9224
Create Date: 2026-05-11 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b1c4f5a2d3e0"
down_revision: Union[str, None] = "aca8a5fb9224"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # voice_provider_assistants — maps provider assistant ID → agent_id
    op.create_table(
        "voice_provider_assistants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_assistant_id", sa.String(length=120), nullable=False, index=True),
        sa.Column("phone_number", sa.String(length=40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_voice_provider_assistants_provider_assistant",
        "voice_provider_assistants",
        ["provider", "provider_assistant_id"],
        unique=True,
    )

    # batch_campaigns — outbound campaign metadata (referenced by voice_calls + batch_recipients)
    op.create_table(
        "batch_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="idle"),
        sa.Column("pacing", sa.Integer(), server_default="12"),
        sa.Column("working_hours_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("platform_users.id")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # batch_recipients — created before voice_calls so call.recipient_id FK can reference it
    op.create_table(
        "batch_recipients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("batch_campaigns.id"), nullable=False, index=True),
        sa.Column("name", sa.String(length=120)),
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("context_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=True),       # FK added later (cross-table cycle)
        sa.Column("attempted_at", sa.DateTime(), nullable=True),
        sa.Column("queue_order", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # voice_calls — one row per phone call
    op.create_table(
        "voice_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_call_id", sa.String(length=120), index=True),
        sa.Column("direction", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ringing"),
        sa.Column("urgency", sa.String(length=8), nullable=True),
        sa.Column("caller_number", sa.String(length=40), nullable=False),
        sa.Column("caller_name", sa.String(length=120), nullable=True),
        sa.Column("caller_tag", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("recording_url", sa.Text(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("escalation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_provider_event", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("batch_campaigns.id"), nullable=True, index=True),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("batch_recipients.id"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # Cross-table FK: batch_recipients.call_id → voice_calls.id (added now that voice_calls exists)
    op.create_foreign_key(
        "fk_batch_recipients_call_id",
        "batch_recipients", "voice_calls",
        ["call_id"], ["id"],
    )

    # voice_call_turns — one row per transcript turn
    op.create_table(
        "voice_call_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("call_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("voice_calls.id"), nullable=False, index=True),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("provider_turn_id", sa.String(length=120), index=True, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # voice_recordings — audio metadata
    op.create_table(
        "voice_recordings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("call_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("voice_calls.id"), nullable=False, unique=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=8), server_default="mp3"),
        sa.Column("signed_url", sa.Text(), nullable=True),
        sa.Column("signed_url_expires_at", sa.DateTime(), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("voice_recordings")
    op.drop_table("voice_call_turns")
    op.drop_constraint("fk_batch_recipients_call_id", "batch_recipients", type_="foreignkey")
    op.drop_table("voice_calls")
    op.drop_table("batch_recipients")
    op.drop_table("batch_campaigns")
    op.drop_index("ix_voice_provider_assistants_provider_assistant",
                  table_name="voice_provider_assistants")
    op.drop_table("voice_provider_assistants")
