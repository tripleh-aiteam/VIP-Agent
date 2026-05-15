"""
chatbot_email_ingest — scheduler tick that pulls new mail and feeds it
through the chatbot reply pipeline.

What runs every 2 minutes (when CHATBOT_EMAIL_POLL_ENABLED=1):

  1. For each agent that has email creds configured, poll the IMAP inbox
     for UNSEEN messages newer than the agent's last_imap_uid watermark.
  2. For each new email:
        a) Find or create a Customer keyed on the sender's email.
        b) Find an existing Conversation by matching the email's
           Message-ID chain or normalized subject against
           Conversation.thread_keys_json. If none, create a new one.
        c) Append a Message(kind="text", author="customer", text=body).
        d) Hand the message to chatbot_reply_service.handle_incoming_message
           with an `on_send` that routes the bot's reply back through SMTP.
        e) Bump the conversation's watermark.

Why we do threading on our side (instead of letting the channel client
handle it): we want the SAME conversation row regardless of channel, so
a customer who emails AND messages on Kakao stays linked. Email is just
another way to reach the customer object.

This is the scaffold path. Production hardening still to do:
  - Move credentials from env vars into chatbot_channel_mappings rows
    (one row per (agent_id, email_account) pair) so the dashboard can
    onboard new mailboxes without a deploy.
  - Replace IMAP polling with Gmail Watch + push notifications for
    sub-second delivery.
  - Attachment handling — currently strips attachments; add them as
    file-kind messages with Supabase Storage uploads."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from db.base import SessionLocal
from db.models import ChatbotConversation, ChatbotCustomer
from services import chatbot_conversation_service as conv_service
from services import chatbot_email_client as email_client
from services import chatbot_reply_service
from services.logger import log


# ============================================================================
#  Scheduler entry point — called by APScheduler tick
# ============================================================================

def poll_all_agents() -> int:
    """Iterate every agent that appears configured for email, poll its
    inbox, ingest new messages. Returns count of messages ingested.

    Discovery for agent list: today we look at chatbot_channel_mappings
    rows with channel="email", falling back to a single agent from the
    global CHATBOT_EMAIL_DEFAULT_AGENT env var if no mappings exist."""
    import os
    db = SessionLocal()
    try:
        agent_ids = _discover_email_agents(db)
        if not agent_ids:
            default_agent = os.getenv("CHATBOT_EMAIL_DEFAULT_AGENT")
            if default_agent:
                agent_ids = [default_agent]
        total = 0
        for aid in agent_ids:
            try:
                total += asyncio.run(_poll_one_agent(db, aid))
            except Exception as e:
                log.warning(
                    f"chatbot_email: poll failed for {aid}: {e}",
                    extra={"action": "chatbot.email_poll_failed", "agent_id": aid},
                )
        if total:
            log.info(
                f"chatbot_email: ingested {total} new message(s) across "
                f"{len(agent_ids)} agent(s)",
                extra={"action": "chatbot.email_polled"},
            )
        return total
    finally:
        db.close()


def _discover_email_agents(db: Session) -> list[str]:
    """Look up agent_ids that have a chatbot_channel_mappings row with
    channel='email'. Empty list if email isn't configured for any agent."""
    from db.models import ChatbotChannelMapping
    rows = (
        db.query(ChatbotChannelMapping)
        .filter(
            ChatbotChannelMapping.channel == "email",
            ChatbotChannelMapping.active.is_(True),
        )
        .all()
    )
    return sorted({r.agent_id for r in rows})


async def _poll_one_agent(db: Session, agent_id: str) -> int:
    """Pull new mail for `agent_id` and ingest each as a Conversation
    Message. Returns count of messages ingested."""
    if not email_client.is_configured(agent_id):
        return 0

    last_uid = _get_global_uid_watermark(db, agent_id)
    inbound = await email_client.poll_inbox_async(agent_id, since_uid=last_uid)
    if not inbound:
        return 0

    ingested = 0
    for em in inbound:
        try:
            await _ingest_email(db, agent_id, em)
            ingested += 1
        except Exception as e:
            log.warning(
                f"chatbot_email: ingest failed for uid={em.uid}: {e}",
                extra={"action": "chatbot.email_ingest_failed"},
            )
    return ingested


def _get_global_uid_watermark(db: Session, agent_id: str) -> Optional[int]:
    """Highest IMAP UID we've already ingested for this agent's email
    channel. None if nothing has been ingested yet."""
    row = (
        db.query(ChatbotConversation.last_imap_uid)
        .filter(
            ChatbotConversation.agent_id == agent_id,
            ChatbotConversation.channel == "email",
            ChatbotConversation.last_imap_uid.isnot(None),
        )
        .order_by(ChatbotConversation.last_imap_uid.desc())
        .first()
    )
    return int(row[0]) if row and row[0] is not None else None


# ============================================================================
#  Per-message ingest
# ============================================================================

async def _ingest_email(
    db: Session, agent_id: str, em: email_client.InboundEmail
) -> None:
    """One inbound email → Customer + Conversation + Message + reply."""
    # Customer
    customer = _find_or_create_customer_by_email(
        db, agent_id, email_addr=em.from_email, name=em.from_name
    )

    # Conversation — match by threading keys (Message-ID chain or subject)
    conv = _find_conversation_by_thread_keys(db, agent_id, em)
    if not conv:
        conv = conv_service.find_or_create_conversation(
            db, agent_id, customer_id=customer.id, channel="email"
        )
    # Stamp threading keys + UID watermark on the conv
    keys = list(conv.thread_keys_json or [])
    for k in email_client.iter_thread_keys(em):
        if k and k not in keys:
            keys.append(k)
    conv.thread_keys_json = keys
    try:
        conv.last_imap_uid = max(int(em.uid), int(conv.last_imap_uid or 0))
    except Exception:
        pass
    db.commit()

    # Append the inbound message
    conv_service.append_message(
        db, agent_id, conv.id,
        author="customer",
        kind="text",
        text=em.body_text or em.subject,
        provider_message_id=em.message_id,
    )

    # Hand to reply pipeline. on_send wraps the SMTP send + threading headers
    # so the bot's reply lands in the same email thread.
    async def _on_send(reply_text: str, _agent_id: str, _conv: ChatbotConversation) -> None:
        await email_client.send_email_async(
            agent_id=_agent_id,
            to_email=em.from_email,
            subject=_reply_subject(em.subject),
            body_text=reply_text,
            in_reply_to=em.message_id,
            references=em.references,
        )

    await chatbot_reply_service.handle_incoming_message(
        db, agent_id, conv,
        incoming_text=em.body_text or em.subject,
        customer=customer,
        on_send=_on_send,
    )


def _find_or_create_customer_by_email(
    db: Session, agent_id: str, *, email_addr: str, name: Optional[str]
) -> ChatbotCustomer:
    existing = (
        db.query(ChatbotCustomer)
        .filter(
            ChatbotCustomer.agent_id == agent_id,
            ChatbotCustomer.email == email_addr,
        )
        .first()
    )
    if existing:
        if name and not existing.name:
            existing.name = name
        existing.last_seen_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    row = ChatbotCustomer(
        agent_id=agent_id,
        name=name or email_addr.split("@")[0],
        email=email_addr,
        last_seen_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _find_conversation_by_thread_keys(
    db: Session, agent_id: str, em: email_client.InboundEmail
) -> Optional[ChatbotConversation]:
    """Search recent email Conversations for one whose `thread_keys_json`
    intersects the new email's threading keys (Message-ID chain + subject)."""
    keys = [k for k in email_client.iter_thread_keys(em) if k]
    if not keys:
        return None
    # JSONB containment query — fast, indexed if a GIN index is added later.
    # For now do a SELECT + Python intersection (works on small thread counts).
    candidates = (
        db.query(ChatbotConversation)
        .filter(
            ChatbotConversation.agent_id == agent_id,
            ChatbotConversation.channel == "email",
            ChatbotConversation.status != "resolved",
        )
        .order_by(ChatbotConversation.last_message_at.desc())
        .limit(50)
        .all()
    )
    key_set = set(keys)
    for conv in candidates:
        existing_keys = set(conv.thread_keys_json or [])
        if existing_keys & key_set:
            return conv
    return None


def _reply_subject(subject: str) -> str:
    """Prepend Re: to outbound subjects unless one is already there."""
    s = (subject or "").strip()
    if not s:
        return "Re:"
    if s.lower().startswith("re:"):
        return s
    return f"Re: {s}"


# Convenience iterable for tests
def configured_agents() -> Iterable[str]:
    db = SessionLocal()
    try:
        return _discover_email_agents(db)
    finally:
        db.close()
