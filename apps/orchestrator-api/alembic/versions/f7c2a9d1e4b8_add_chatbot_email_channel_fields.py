"""add_chatbot_email_channel_fields

Adds the columns required to treat email as a chatbot channel:
  - chatbot_customers.email          → channel identifier for inbound mail
  - chatbot_conversations.thread_keys_json → list of RFC Message-IDs +
    normalized subject keys for threading new mail into existing convs
  - chatbot_conversations.last_imap_uid    → watermark for the IMAP poller

Revision ID: f7c2a9d1e4b8
Revises: e9f1b4c8a2d6
Create Date: 2026-05-14 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f7c2a9d1e4b8"
down_revision: Union[str, None] = "e9f1b4c8a2d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chatbot_customers",
        sa.Column("email", sa.String(length=254), nullable=True),
    )
    op.create_index(
        "ix_chatbot_customers_email",
        "chatbot_customers",
        ["email"],
    )

    op.add_column(
        "chatbot_conversations",
        sa.Column(
            "thread_keys_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "chatbot_conversations",
        sa.Column("last_imap_uid", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chatbot_conversations", "last_imap_uid")
    op.drop_column("chatbot_conversations", "thread_keys_json")
    op.drop_index("ix_chatbot_customers_email", table_name="chatbot_customers")
    op.drop_column("chatbot_customers", "email")
