"""add_chatbot_inbox_tables

Chatbot Inbox — v1.3.0 schema. Five tables for the customer-facing
chatbot surface: customers, conversations, messages, conversation
actions (audit log), and channel mappings (multi-tenant routing).

Mirrors the voice-call domain pattern (every row has agent_id, indexed).

Revision ID: c2e8a3f1d5b9
Revises: b1c4f5a2d3e0
Create Date: 2026-05-12 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c2e8a3f1d5b9"
down_revision: Union[str, None] = "b1c4f5a2d3e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # chatbot_customers — one row per customer per agent
    op.create_table(
        "chatbot_customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("name", sa.String(length=120)),
        sa.Column("phone", sa.String(length=40), index=True),
        sa.Column("kakao_user_id", sa.String(length=120), index=True),
        sa.Column("tag", sa.String(length=120)),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("tags_json", postgresql.JSONB(astext_type=sa.Text()), server_default="[]"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    # Composite index — finding a customer by (agent_id, phone) is the
    # primary lookup path from the Kakao webhook handler
    op.create_index(
        "ix_chatbot_customers_agent_phone",
        "chatbot_customers",
        ["agent_id", "phone"],
    )
    op.create_index(
        "ix_chatbot_customers_agent_kakao",
        "chatbot_customers",
        ["agent_id", "kakao_user_id"],
    )

    # chatbot_conversations — must come before voice_calls FK references would fail;
    # but voice_calls.id already exists, so the FK from chatbot_conversations.voice_call_id
    # to voice_calls.id is safe to add here.
    op.create_table(
        "chatbot_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("channel", sa.String(length=12), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chatbot_customers.id"), nullable=False, index=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="needs_reply"),
        sa.Column("urgency", sa.String(length=8), nullable=True),
        sa.Column("last_message_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("unread_count", sa.Integer(), server_default="0"),
        sa.Column("preview", sa.Text(), nullable=True),
        sa.Column("suggested_reply_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("escalation_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("voice_call_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("voice_calls.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    # Composite index — list view queries: scope by agent_id + sort by last_message_at desc
    op.create_index(
        "ix_chatbot_conversations_agent_last",
        "chatbot_conversations",
        ["agent_id", "last_message_at"],
    )
    op.create_index(
        "ix_chatbot_conversations_agent_status",
        "chatbot_conversations",
        ["agent_id", "status"],
    )

    # chatbot_messages — one row per message
    op.create_table(
        "chatbot_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chatbot_conversations.id"), nullable=False, index=True),
        sa.Column("author", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("provider_message_id", sa.String(length=120), index=True, nullable=True),
        # text content
        sa.Column("text", sa.Text(), nullable=True),
        # voice
        sa.Column("voice_url", sa.Text(), nullable=True),
        sa.Column("voice_duration_sec", sa.Integer(), nullable=True),
        sa.Column("voice_transcript", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        # image
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("image_caption", sa.Text(), nullable=True),
        sa.Column("image_width", sa.Integer(), nullable=True),
        sa.Column("image_height", sa.Integer(), nullable=True),
        # file
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=200), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_mime", sa.String(length=80), nullable=True),
        # bot metadata
        sa.Column("bot_meta_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("partial", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # chatbot_conversation_actions — audit log
    op.create_table(
        "chatbot_conversation_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("chatbot_conversations.id"), nullable=False, index=True),
        sa.Column("at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.String(length=120), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("platform_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # chatbot_channel_mappings — Kakao Channel ID → agent_id resolution
    op.create_table(
        "chatbot_channel_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False, index=True),
        sa.Column("channel", sa.String(length=12), nullable=False),
        sa.Column("provider_channel_id", sa.String(length=120), nullable=False, index=True),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("api_key_env_var", sa.String(length=80), nullable=True),
        sa.Column("webhook_secret_env_var", sa.String(length=80), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_chatbot_channel_mappings_provider",
        "chatbot_channel_mappings",
        ["channel", "provider_channel_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_chatbot_channel_mappings_provider", table_name="chatbot_channel_mappings")
    op.drop_table("chatbot_channel_mappings")
    op.drop_table("chatbot_conversation_actions")
    op.drop_table("chatbot_messages")
    op.drop_index("ix_chatbot_conversations_agent_status", table_name="chatbot_conversations")
    op.drop_index("ix_chatbot_conversations_agent_last", table_name="chatbot_conversations")
    op.drop_table("chatbot_conversations")
    op.drop_index("ix_chatbot_customers_agent_kakao", table_name="chatbot_customers")
    op.drop_index("ix_chatbot_customers_agent_phone", table_name="chatbot_customers")
    op.drop_table("chatbot_customers")
