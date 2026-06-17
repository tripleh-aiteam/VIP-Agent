"""
VIP AI Platform — Twin-to-Twin communication (Phase 3).

Lets twins talk to each other:
  • send_message  — a twin sends a plain message (optionally with a file) to another
  • ask_twin      — a twin asks another twin a question; the other twin's BRAIN
                    answers from its own knowledge (the "twins talking" magic)
  • discuss       — several twins respond to one topic, each in their own voice,
                    seeing what the others said (an async group discussion)

Everything is stored in twin_peer_messages (grouped by thread_id) so it's
auditable and can be shown in the portal. Answers use twin_brain.think, so each
twin replies using its own private knowledge — staying in character.
"""

import uuid as _uuidlib
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from db.models import TwinPeerMessage, DigitalTwin
from services import twin_service, twin_brain
from services.logger import log


def _name(db: Session, twin_id) -> str:
    t = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    return t.name if t else "Unknown twin"


def _peer_help_on(db: Session, twin_id) -> bool:
    t = db.query(DigitalTwin).filter(DigitalTwin.id == twin_id).first()
    return bool(t and getattr(t, "peer_help_enabled", False))


def send_message(db: Session, from_twin_id, to_twin_id, content: str,
                 attachment_name: Optional[str] = None, attachment_text: Optional[str] = None,
                 kind: str = "message", thread_id=None) -> TwinPeerMessage:
    msg = TwinPeerMessage(
        from_twin_id=from_twin_id, to_twin_id=to_twin_id,
        thread_id=thread_id, kind=kind,
        content=(content or "").strip()[:8000],
        attachment_name=(attachment_name or None),
        attachment_text=(attachment_text or None),
    )
    db.add(msg)
    db.flush()
    try:
        twin_service.log_activity(db, from_twin_id, "peer_message",
                                  f"Messaged {_name(db, to_twin_id)}: {content[:60]}",
                                  {"to_twin_id": str(to_twin_id), "kind": kind})
    except Exception:
        pass
    db.commit()
    return msg


def ask_twin(db: Session, from_twin_id, to_twin_id, question: str) -> dict:
    """from_twin asks to_twin a question; to_twin's brain answers from its own
    knowledge. Both are stored under one thread. Returns {thread_id, question, answer}."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty question"}
    # Privacy wall: a twin only answers peer questions if its owner opted in.
    if not _peer_help_on(db, to_twin_id):
        return {"ok": False,
                "error": f"{_name(db, to_twin_id)} hasn't enabled helping other twins. "
                         "Their owner can turn on 'Help other twins' in Settings."}
    thread_id = _uuidlib.uuid4()
    asker = _name(db, from_twin_id)
    # Store the question (asker -> target)
    send_message(db, from_twin_id, to_twin_id, question, kind="question", thread_id=thread_id)
    # The target twin answers using ITS brain/knowledge, told who is asking.
    prompt = (f"Your colleague {asker} (their AI twin) is asking you: \"{question}\"\n"
              f"Answer helpfully from what you know, in your own voice. Be concise.")
    try:
        answer = twin_brain.think(db, to_twin_id, prompt) or "(no answer)"
    except Exception as e:
        answer = f"(could not answer: {e})"
        log.warning(f"ask_twin: brain failed: {e}")
    # Store the answer (target -> asker, same thread)
    send_message(db, to_twin_id, from_twin_id, answer, kind="answer", thread_id=thread_id)
    return {"ok": True, "thread_id": str(thread_id),
            "question": question, "answer": answer,
            "from": asker, "to": _name(db, to_twin_id)}


def discuss(db: Session, topic: str, twin_ids: list, rounds: int = 1) -> dict:
    """Each twin responds to `topic`, seeing prior responses. Returns the thread."""
    topic = (topic or "").strip()
    # Privacy wall: only twins whose owners opted in may participate.
    twin_ids = [t for t in (twin_ids or [])[:8] if _peer_help_on(db, t)]
    if not topic or len(twin_ids) < 2:
        return {"ok": False, "error": "Need a topic and at least 2 twins that have enabled 'Help other twins'."}
    thread_id = _uuidlib.uuid4()
    transcript = []
    for _ in range(max(1, min(rounds, 3))):
        for tid in twin_ids:
            prior = "\n".join(f"- {r['twin']}: {r['content']}" for r in transcript[-8:])
            prompt = (f"Group discussion topic: \"{topic}\".\n"
                      + (f"What others have said so far:\n{prior}\n\n" if prior else "")
                      + "Add YOUR perspective in 2-3 sentences, in your own voice. "
                        "Build on or respectfully challenge the others; don't repeat.")
            try:
                reply = twin_brain.think(db, tid, prompt) or ""
            except Exception as e:
                reply = ""
                log.warning(f"discuss: brain failed for {tid}: {e}")
            if reply.strip():
                name = _name(db, tid)
                send_message(db, tid, None, reply, kind="discussion", thread_id=thread_id)
                transcript.append({"twin_id": str(tid), "twin": name, "content": reply.strip()})
    return {"ok": True, "thread_id": str(thread_id), "topic": topic, "transcript": transcript}


def inbox(db: Session, twin_id, limit: int = 50) -> list:
    """Messages addressed to this twin (incl. discussions it took part in)."""
    rows = (db.query(TwinPeerMessage)
            .filter(or_(TwinPeerMessage.to_twin_id == twin_id,
                        TwinPeerMessage.from_twin_id == twin_id))
            .order_by(TwinPeerMessage.created_at.desc())
            .limit(limit).all())
    out = []
    for m in rows:
        out.append({
            "id": str(m.id), "thread_id": str(m.thread_id) if m.thread_id else None,
            "kind": m.kind,
            "from_twin_id": str(m.from_twin_id), "from_name": _name(db, m.from_twin_id),
            "to_twin_id": str(m.to_twin_id) if m.to_twin_id else None,
            "content": m.content,
            "attachment_name": m.attachment_name,
            "direction": "in" if str(m.to_twin_id) == str(twin_id) else "out",
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return out
