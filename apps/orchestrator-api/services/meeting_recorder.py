"""
VIP AI Platform — Meeting Recorder Service
Records meetings, transcribes voice, generates bilingual summaries (Korean + English).

Flow:
1. User starts recording in browser (MediaRecorder API)
2. Audio sent to backend as text transcript (Web Speech API does transcription in browser)
3. Backend generates summary using LLM in Korean + English
4. Summary saved to meeting minutes + twin knowledge
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from db.models import Meeting, MeetingMinutes, DigitalTwin
from services import twin_service
from services.llm_client import chat_completion_sync
from services.logger import log


def generate_meeting_summary(
    db: Session,
    transcript: str,
    meeting_title: str = "Meeting",
    meeting_id: Optional[UUID] = None,
    participants: Optional[list[str]] = None,
) -> dict:
    """
    Generate bilingual meeting summary from transcript.
    Returns: Korean summary + English summary + action items + decisions.
    """

    if not transcript or len(transcript.strip()) < 20:
        return {"error": "Transcript too short to summarize"}

    # Generate English summary
    english_prompt = f"""Analyze this meeting transcript and create a structured summary.

MEETING: {meeting_title}
PARTICIPANTS: {', '.join(participants or ['Unknown'])}
TRANSCRIPT:
{transcript[:3000]}

Create a summary with these sections:
1. **Meeting Overview** (2-3 sentences)
2. **Key Discussion Points** (bullet points)
3. **Decisions Made** (bullet points — what was decided)
4. **Action Items** (who does what by when)
5. **Open Questions** (unresolved items)
6. **Next Steps** (what happens after this meeting)

Be concise and clear."""

    english_summary = chat_completion_sync(
        system_prompt="You are a professional meeting note-taker. Create clear, structured meeting summaries.",
        messages=[{"role": "user", "content": english_prompt}],
        max_tokens=500,
        temperature=0.3,
    )

    # Generate Korean summary
    korean_prompt = f"""다음 회의 내용을 한국어로 요약해주세요.

회의 제목: {meeting_title}
참석자: {', '.join(participants or ['Unknown'])}
회의 내용:
{transcript[:3000]}

다음 형식으로 요약해주세요:
1. **회의 개요** (2-3문장)
2. **주요 논의 사항** (항목별)
3. **결정 사항** (항목별 — 무엇이 결정되었는지)
4. **실행 항목** (누가 무엇을 언제까지)
5. **미해결 사항** (해결되지 않은 항목)
6. **다음 단계** (회의 이후 진행 사항)

간결하고 명확하게 작성해주세요."""

    korean_summary = chat_completion_sync(
        system_prompt="당신은 전문 회의록 작성자입니다. 명확하고 체계적인 회의 요약을 작성해주세요.",
        messages=[{"role": "user", "content": korean_prompt}],
        max_tokens=500,
        temperature=0.3,
    )

    # Extract action items
    actions_prompt = f"""From this transcript, extract ONLY the action items in JSON format:

{transcript[:2000]}

Return as JSON array: [{{"who": "name", "task": "what to do", "deadline": "when"}}]
If no clear action items, return empty array: []"""

    actions_text = chat_completion_sync(
        system_prompt="Extract action items from meeting transcripts. Return valid JSON only.",
        messages=[{"role": "user", "content": actions_prompt}],
        max_tokens=200,
        temperature=0.1,
    )

    # Parse actions
    import json
    actions = []
    try:
        # Try to find JSON in the response
        start = actions_text.find("[")
        end = actions_text.rfind("]") + 1
        if start >= 0 and end > start:
            actions = json.loads(actions_text[start:end])
    except Exception:
        actions = []

    # Save to meeting if meeting_id provided
    if meeting_id:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if meeting:
            minutes = MeetingMinutes(
                meeting_id=meeting_id,
                decisions=[],
                tasks_assigned=actions,
                open_questions=[],
                summary=f"## English Summary\n\n{english_summary}\n\n---\n\n## 한국어 요약\n\n{korean_summary}",
            )
            db.add(minutes)
            db.flush()

    result = {
        "meeting_title": meeting_title,
        "generated_at": datetime.utcnow().isoformat(),
        "transcript_length": len(transcript),
        "english_summary": english_summary,
        "korean_summary": korean_summary,
        "action_items": actions,
        "participants": participants or [],
    }

    return result


def save_meeting_to_twin_knowledge(
    db: Session,
    twin_id: UUID,
    meeting_title: str,
    english_summary: str,
    korean_summary: str,
    action_items: list,
):
    """Save meeting summary to twin's knowledge base."""
    content = f"Meeting: {meeting_title}\nDate: {datetime.utcnow().strftime('%Y-%m-%d')}\n\n"
    content += f"Summary:\n{english_summary[:500]}\n\n"
    if action_items:
        content += "Action Items:\n"
        for a in action_items:
            content += f"- {a.get('who','?')}: {a.get('task','?')} (by {a.get('deadline','TBD')})\n"

    twin_service.add_knowledge(
        db, twin_id,
        title=f"Meeting notes: {meeting_title} ({datetime.utcnow().strftime('%b %d')})",
        content=content,
        source_type="document",
    )

    twin_service.log_activity(
        db, twin_id, "auto_learn",
        f"Learned from meeting: {meeting_title}",
        {"source": "meeting_recording", "title": meeting_title},
    )
    db.flush()
