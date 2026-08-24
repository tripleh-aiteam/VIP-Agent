"""
VIP AI Platform — Chat Router
POST /chat/sessions, GET /chat/sessions, GET /chat/sessions/{id},
POST /chat/sessions/{id}/messages, GET /chat/sessions/{id}/messages, GET /chat/health
"""

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Header
from sqlalchemy.orm import Session

from db.base import get_db
from services import chat_service
from services.intent_service import classify, classify_batch
from services.api_security import rate_limit_compose

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/reco-evidence/{ticker}")
def reco_evidence(ticker: str, lang: str = Query("ko"), db: Session = Depends(get_db)):
    """Content for the chat's RIGHT-side proof panel (boss 2026-08-24: clicking
    evidence must SHOW the data — checklist scores with item numbers, 일봉 zone,
    분봉/실시간, 거래량, clickable news — beside a TradingView chart, not re-ask).
    By CODE, so no name resolution can miss."""
    from services.checklist_reco import detail_by_code
    r = detail_by_code(db, str(ticker), lang=lang)
    return {"ok": bool(r), "ticker": str(ticker).zfill(6), "reply": r or ""}


# ---------------------------------------------------------------------------
# Voice Assistant ("Chatbot") — single endpoint for Web Speech API integration
# ---------------------------------------------------------------------------

class VoiceCommandBody(BaseModel):
    transcript: str = Field(..., description="What the user said (Web Speech API transcript)")
    language: Optional[str] = Field("auto", description="'en', 'ko', or 'auto' for language detection")


@router.post("/voice")
def voice_command(body: VoiceCommandBody, db: Session = Depends(get_db)):
    """Boss voice command endpoint — used by the floating Chatbot overlay.
    Returns a short, voice-friendly reply that the browser can speak via SpeechSynthesis."""
    from services.voice_intents import handle_voice_command
    result = handle_voice_command(db, body.transcript, body.language or "auto")
    return result


@router.get("/price/live")
def live_price(codes: str = Query(..., description="Comma-separated KR 6-digit codes, e.g. 000660,035420")):
    """Live KR price(s) — Kiwoom during the KRX session (09:00–15:30 KST), Naver
    after-market. VIP holds the Kiwoom key, so the Stock-advisor backend calls this
    to show the SAME Kiwoom price as VIP — centralizing Kiwoom on one key (no token
    contention) without needing creds on the Stock side. Each quote:
    {code, name, price, change_pct, source}."""
    from services.assistant_agent import _live_price_for_code, _canon_price_src
    wanted = [c.strip() for c in (codes or "").split(",") if c.strip().isdigit()][:12]
    quotes = []
    for c in wanted:
        q = _live_price_for_code(c, None)
        if q:
            # Canonical source token (kiwoom / naver_nxt / naver) so the Stock app
            # relays the SAME source label as VIP without re-deriving it.
            q["src"] = _canon_price_src(q.get("source"))
            quotes.append(q)
    return {"ok": bool(quotes), "count": len(quotes), "quotes": quotes}


@router.get("/price/answer")
def price_answer(q: str = Query(..., description="The user's price question, verbatim"),
                 lang: str = Query("auto", description="'en', 'ko', or 'auto'"),
                 db: Session = Depends(get_db)):
    """Fully-FORMATTED current-price answer (opening/current/high/low as asked, Kiwoom
    during market / Naver after, with source label). VIP owns the Kiwoom key, so the
    Stock app relays this string for price questions → both surfaces read identically.
    Returns {ok, reply}."""
    from services.assistant_agent import _vip_live_price_reply
    r = _vip_live_price_reply(q or "", lang or "auto", db)
    if r and r.get("reply"):
        return {"ok": True, "reply": r["reply"]}
    return {"ok": False, "reply": ""}


@router.get("/stock/answer")
def stock_data_answer(q: str = Query(..., description="The user's stock-data question, verbatim"),
                      lang: str = Query("auto", description="'en', 'ko', or 'auto'"),
                      db: Session = Depends(get_db)):
    """Unified, fully-FORMATTED stock-data answer — current price (Kiwoom/Naver, with
    volume + any requested fields) OR a deterministic multi-day history table (past
    dates / ranges like 'last 4 days'). VIP is the single source, so the AI Advisor
    relays this for ALL data questions → both surfaces read IDENTICALLY. Returns
    {ok, reply}."""
    from services.assistant_agent import _vip_stock_data_reply
    reply = _vip_stock_data_reply(q or "", lang or "auto", db)
    if reply:
        return {"ok": True, "reply": reply}
    return {"ok": False, "reply": ""}


@router.get("/advice/answer")
def advice_answer(q: str = Query(..., description="A future-outlook or buy/sell-decision question"),
                  lang: str = Query("auto"), db: Session = Depends(get_db)):
    """VIP's full-agent answer for OUTLOOK / RECOMMENDATION questions — the deterministic
    two-method block ('5-day outlook') or the decision agent ('buy or sell?'). The AI
    Advisor relays this so future/decision answers read IDENTICALLY to VIP. {ok, reply}."""
    from services.assistant_agent import run_agent
    try:
        r = run_agent(db, q or "", language=lang or "auto", agent_id="vip")
        rep = (r.get("reply") or "").strip()
        # Accept every trading-assistant intent (not just the two-method/decide chain) so
        # position_advice / scalp / scalp_watchlist relay correctly to the AI Advisor —
        # only genuinely empty/error turns return ok:false (caller then falls back locally).
        _bad = {"empty", "error", "multimodal_failed", "multimodal_missing", "chain_empty"}
        if rep and r.get("intent") not in _bad:
            return {"ok": True, "reply": rep, "intent": r.get("intent")}
    except Exception:
        pass
    return {"ok": False, "reply": ""}


@router.get("/shortselling/live")
def live_short_selling(codes: str = Query(..., description="Comma-separated KR 6-digit codes")):
    """Latest 공매도 (short-selling) figures via Kiwoom ka10014 — VIP holds the Kiwoom
    key, so the Stock-advisor backend relays this to show the SAME data without creds.
    Each item: {name, short_volume, short_ratio, short_value, date}."""
    from services.assistant_agent import _short_selling_for_code, _fmt_short_date
    wanted = [c.strip() for c in (codes or "").split(",") if c.strip().isdigit()][:12]
    items = []
    for c in wanted:
        it = _short_selling_for_code(c, None)
        if it:
            it["date"] = _fmt_short_date(it.get("date"))
            items.append(it)
    return {"ok": bool(items), "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# /chat/agent — Tool-calling agent (Notion-AI-style)
#
# Same response shape as /chat/voice but powered by the assistant_agent
# tool-calling loop. The LLM (Groq Llama 3.3 70B) sees a full tool catalog
# + page manifest and picks the right capability for any natural query —
# no keyword lists, no hardcoded intents. Use this for the next-gen
# Assistant widget.
# ---------------------------------------------------------------------------

class AgentCommandBody(BaseModel):
    transcript: str = Field("", description="What the user said or typed (can be empty if confirming)")
    language: Optional[str] = Field("auto", description="'en', 'ko', or 'auto'")
    current_path: Optional[str] = Field(None, description="The page the user is currently on (for 'this' references)")
    selected_id: Optional[str] = Field(None, description="ID of the currently-selected item on the page (conversation_id on /chatbot, report_id on /reports/<id>, etc.) — used for 'this' references")
    history: Optional[list[dict]] = Field(None, description="Optional turn history for context")
    confirmed_tool: Optional[str] = Field(None, description="If set, bypass LLM and execute this tool directly (user confirmed a previously-proposed write action)")
    confirmed_args: Optional[dict] = Field(None, description="Args for the confirmed_tool")
    attachment_ids: Optional[list[str]] = Field(None, description="IDs returned from POST /chatbot/upload — included so the assistant can use the file content (image/pdf/text) when answering. When present, the agent auto-routes to Gemini 2.5 Pro multimodal.")
    model: Optional[str] = Field(None, description="Optional override — pin a specific LLM for this request (e.g. 'claude-sonnet-4-6'). Bypasses the smart router. Useful for the in-overlay model picker dropdown.")
    user_id: Optional[str] = Field(None, description="Caller user id (email) used for cross-session memory. Each user gets their own rolling 'assistant_overlay' chat session that recall_history searches. Defaults to 'boss' when unset.")
    agentId: Optional[str] = Field(None, description="Which agent's knowledge base to consult (vip / realty / asset / ...). Defaults to 'vip'.")
    page_context: Optional[str] = Field(None, description="Snapshot of the user's current page DOM text — what they see on screen right now. Captured by the frontend AssistantCard and forwarded so the LLM can answer questions like 'how much total asset?' without needing a separate API/tool call. Capped at ~15K chars on the frontend side.")


@router.post("/agent")
def agent_command(
    body: AgentCommandBody,
    db: Session = Depends(get_db),
    x_user_email: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
):
    # Wrap the implementation so unhandled exceptions surface to the
    # browser as JSON errors (not the opaque 'Internal Server Error') —
    # makes the network tab actually useful when something explodes.
    try:
        # Twin work-assistant: agentId "twin:<uuid>" routes to that worker's
        # OWN private twin brain (owner-auth required — privacy wall).
        if (body.agentId or "").startswith("twin:"):
            return _twin_agent_reply(body, db, x_user_email, x_user_token)
        return _agent_command_impl(body, db)
    except Exception as e:
        import traceback as _tb
        from fastapi.responses import JSONResponse as _JSON
        tb = _tb.format_exc()[-1500:]
        return _JSON(
            status_code=500,
            content={
                "intent": "error",
                "language": body.language or "en",
                "reply": f"[server error] {e.__class__.__name__}: {str(e)[:200]}",
                "error": str(e)[:400],
                "traceback": tb,
            },
        )


def _twin_agent_reply(body: AgentCommandBody, db: Session, x_user_email, x_user_token):
    """Route an Assistant-widget request to the worker's own twin brain.
    Owner-only (privacy wall). Returns the widget's expected {reply, ...} shape."""
    from uuid import UUID as _UUID
    from services import auth_service, twin_brain
    twin_id_str = (body.agentId or "").split(":", 1)[1].strip()
    lang = body.language or "en"
    # Privacy wall: only the twin's owner may use it.
    user = auth_service.verify_session_token(db, x_user_email, x_user_token) if (x_user_email and x_user_token) else None
    if not (user and getattr(user, "twin_id", None) and str(user.twin_id) == twin_id_str):
        return {"reply": "You can only use your own twin.", "language": lang, "intent": "error", "source": "fallback"}
    twin_id = _UUID(twin_id_str)
    message = body.transcript or ""
    # Pull text from any uploaded attachments so the twin can use the files.
    if body.attachment_ids:
        try:
            from routers.chatbot import load_attachment
            from services.assistant_agent import _extract_attachment_text
            parts = []
            for aid in body.attachment_ids[:5]:
                a = load_attachment(aid)
                if not a:
                    continue
                txt = _extract_attachment_text(a.get("filename", ""), a.get("mime_type", ""), a.get("blob", b""))
                if txt:
                    parts.append(f"[file: {a.get('filename','')}]\n{txt[:6000]}")
            if parts:
                message = (message + "\n\n" + "\n\n".join(parts)).strip()
        except Exception:
            pass
    reply = twin_brain.think(db, twin_id, message, model=body.model)
    return {"reply": reply or "", "language": lang, "intent": None, "source": "llm"}


def _agent_command_impl(body: AgentCommandBody, db: Session):
    """LLM-driven assistant with full tool catalog. Replaces keyword routing.

    Two modes:
      - Discovery (default): LLM picks a tool and either executes (read) or
        proposes (write) for user confirmation.
      - Confirmed execute: pass confirmed_tool + confirmed_args; backend
        bypasses LLM and runs the write action.
    """
    from services.assistant_agent import run_agent
    return run_agent(
        db,
        transcript=body.transcript or "",
        language=body.language or "auto",
        current_path=body.current_path,
        selected_id=body.selected_id,
        history=body.history,
        confirmed_tool=body.confirmed_tool,
        confirmed_args=body.confirmed_args,
        attachment_ids=body.attachment_ids,
        forced_model=body.model,
        user_id=body.user_id or "boss",
        agent_id=body.agentId or "vip",
        page_context=body.page_context,
    )


@router.post("/agent/stream")
def agent_command_stream(
    body: AgentCommandBody,
    db: Session = Depends(get_db),
    x_user_email: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
):
    """Same as /chat/agent but returns the reply as a Server-Sent Events
    stream so the overlay renders it Notion-AI-style — text appearing
    word-by-word instead of all at once.

    Implementation: we run the full tool-calling pipeline up front (fast),
    then chunk the FINAL reply into small slices and emit them as SSE.
    The trailing event carries the action / suggestions / proposed_action
    metadata so the overlay can finalise the turn.

    Output protocol — one JSON object per `data:` line:
      {"delta": "<chunk>"}              # streaming text chunks
      {"intent": "<name>"}              # surfaced once, mid-stream
      {"done": true, ...rest}           # final event with metadata
    """
    from fastapi.responses import StreamingResponse
    import json as _json
    import time as _time

    _is_twin = (body.agentId or "").startswith("twin:")

    def gen():
        # Compute in a worker thread (its own DB session) while emitting SSE
        # keepalive comments, so the connection stays alive during slow model
        # work and the browser never reports "Failed to fetch".
        import threading
        from db.base import SessionLocal as _SL
        box: dict = {}

        def _compute():
            s = _SL()
            try:
                if _is_twin:
                    box["r"] = _twin_agent_reply(body, s, x_user_email, x_user_token)
                else:
                    from services.assistant_agent import run_agent
                    box["r"] = run_agent(
                        s,
                        transcript=body.transcript or "",
                        language=body.language or "auto",
                        current_path=body.current_path,
                        selected_id=body.selected_id,
                        history=body.history,
                        confirmed_tool=body.confirmed_tool,
                        confirmed_args=body.confirmed_args,
                        attachment_ids=body.attachment_ids,
                        forced_model=body.model,
                        user_id=body.user_id or "boss",
                        agent_id=body.agentId or "vip",
                        page_context=body.page_context,
                    )
            except Exception as e:
                box["r"] = {"reply": f"[error] {e.__class__.__name__}: {str(e)[:200]}",
                            "intent": "error", "language": body.language or "en"}
            finally:
                s.close()

        th = threading.Thread(target=_compute, daemon=True)
        th.start()
        while th.is_alive():
            yield ": keepalive\n\n"   # SSE comment — clients ignore it
            _time.sleep(2)
        th.join(timeout=1)

        result = box.get("r") or {"reply": ""}
        reply = str(result.get("reply") or "")
        if result.get("intent"):
            yield f"data: {_json.dumps({'intent': result['intent']})}\n\n"
        words = reply.split(" ")
        for i, w in enumerate(words):
            chunk = w + (" " if i < len(words) - 1 else "")
            yield f"data: {_json.dumps({'delta': chunk})}\n\n"
            _time.sleep(0.01)
        trailing = {
            "done": True,
            "intent": result.get("intent"),
            "action": result.get("action"),
            "tool_used": result.get("tool_used"),
            "tool_result": result.get("tool_result"),
            "card": result.get("card"),
            "suggestions": result.get("suggestions"),
            "proposed_action": result.get("proposed_action"),
            "language": result.get("language"),
            "transcript": result.get("transcript"),
        }
        yield f"data: {_json.dumps(trailing, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # nginx — disable buffering
        },
    )


# ---------------------------------------------------------------------------
#  Self-improvement: feedback on assistant replies (👍/👎 + correction)
# ---------------------------------------------------------------------------

# Allowlist of known agents. Insights/feedback endpoints validate agentId
# against this so a caller can't read or write an arbitrary namespace (C1).
KNOWN_AGENTS = {"vip", "stock", "realty", "asset", "aiglass"}


def _valid_agent(agent_id: Optional[str]) -> str:
    a = (agent_id or "vip").strip().lower()
    if a not in KNOWN_AGENTS:
        raise HTTPException(status_code=400, detail=f"unknown agentId '{a}'")
    return a


class FeedbackBody(BaseModel):
    agentId: str = Field("vip", description="Which agent the feedback is for (scopes learning to its KB)")
    question: str = Field("", description="The user's question that produced the reply")
    answer: str = Field("", description="The assistant reply being rated")
    verdict: str = Field(..., description="'up' (👍) or 'down' (👎)")
    correction: Optional[str] = Field(None, description="For 👎: what the right answer/behaviour was")
    user_id: Optional[str] = Field(None, description="Caller id for attribution")


@router.post("/feedback")
def agent_feedback(body: FeedbackBody, db: Session = Depends(get_db)):
    """Record thumbs feedback on an assistant reply and feed the self-improvement
    loop: 👍 saves a verified exemplar, 👎 + correction is judged then (if sound)
    stored as a lesson in the agent's knowledge base so the mistake isn't
    repeated. Never raises — returns {ok, learned, ...}."""
    try:
        from services.assistant_learning import learn_from_feedback
        agent_id = _valid_agent(body.agentId)
        verdict = "up" if str(body.verdict).lower() in ("up", "👍", "good", "yes") else "down"
        result = learn_from_feedback(
            db,
            agent_id=agent_id,
            question=body.question or "",
            answer=body.answer or "",
            verdict=verdict,
            correction=body.correction,
            user_id=body.user_id,
        )
        return {"ok": True, **result}
    except HTTPException:
        raise  # let agentId-allowlist 400 propagate
    except Exception as e:
        from fastapi.responses import JSONResponse as _JSON
        return _JSON(status_code=200, content={"ok": False, "learned": False, "error": str(e)[:300]})


# ---------------------------------------------------------------------------
#  Self-improvement insights — knowledge gaps, quality metrics, manual cycle
# ---------------------------------------------------------------------------

@router.get("/insights/gaps")
def insights_gaps(agentId: str = Query("vip"), days: int = Query(30, ge=1, le=180), db: Session = Depends(get_db)):
    """Clusters of low-confidence questions → what knowledge to upload (#13)."""
    try:
        from services.assistant_learning import knowledge_gaps
        return knowledge_gaps(db, agent_id=_valid_agent(agentId), days=days)
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "clusters": []}


@router.get("/insights/metrics")
def insights_metrics(agentId: str = Query("vip"), days: int = Query(30, ge=1, le=180), db: Session = Depends(get_db)):
    """Quality metrics for the self-improvement dashboard (#15)."""
    try:
        from services.assistant_learning import quality_metrics
        return quality_metrics(db, agent_id=_valid_agent(agentId), days=days)
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.post("/insights/improve-now", dependencies=[Depends(rate_limit_compose)])
def insights_improve_now(agentId: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Manually trigger the self-improvement cycle (#14) — research the top
    recurring low-confidence questions and learn them. Omit agentId for all.
    Rate-limited (10/min/IP) to prevent cost-DoS via repeated LLM+web spend."""
    try:
        from services.assistant_learning import nightly_improve_cycle
        agents = [_valid_agent(agentId)] if agentId else None
        return nightly_improve_cycle(db, agents=agents)
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.get("/agent/manifest")
def agent_manifest():
    """Expose the assistant manifest (pages + external agents) as JSON so
    the frontend can render dynamic page lists / hint chips without
    duplicating the catalog."""
    from services.assistant_manifest import (
        get_all_pages, get_external_agents, get_agent_identity,
    )
    drift = {"ok": True, "message": "drift check unavailable", "skipped": True}
    try:
        from services.assistant_manifest import detect_sidebar_drift
        drift = detect_sidebar_drift()
    except Exception as e:
        drift = {"ok": False, "error": f"drift check failed: {e}"}
    return {
        # Identity — surfaced so a consumer agent can verify which branding
        # the backend is responding under (sanity check after a rebrand).
        "identity": get_agent_identity(),
        "pages": get_all_pages(),
        "external_agents": [
            {"name": a["name"], "name_ko": a.get("name_ko"),
             "description": a["description"], "portal_url": a["portal_url"],
             "keywords": a.get("keywords", [])}
            for a in get_external_agents()
        ],
        "drift": drift,
    }


@router.get("/agent/tools")
def agent_tools():
    """List every tool the assistant can call — useful for debugging and
    for the frontend to show 'what the assistant can do'."""
    from services.assistant_tools import list_tool_schemas
    schemas = list_tool_schemas()
    return {
        "tool_count": len(schemas),
        "read_count": sum(1 for s in schemas if s["kind"] == "read"),
        "write_count": sum(1 for s in schemas if s["kind"] == "write"),
        "tools": schemas,
    }


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Accept an audio blob (webm/ogg/mp3/wav) and transcribe.
    Tries OpenAI Whisper first; falls back to Gemini 2.5 Flash audio understanding
    if Whisper is unavailable (quota / no key). Used by the Chatbot overlay as a
    reliable alternative to Chrome's Web Speech API.
    """
    import os
    import base64
    import httpx

    audio_bytes = await file.read()
    if len(audio_bytes) < 200:
        return {"transcript": "", "language": "auto", "reason": "audio too short"}

    content_type = file.content_type or "audio/webm"

    # --- Attempt 1: OpenAI Whisper ---
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    files={"file": (file.filename or "audio.webm", audio_bytes, content_type)},
                    data={"model": "whisper-1"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {"transcript": (data.get("text") or "").strip(),
                            "language": data.get("language", "auto"),
                            "engine": "whisper"}
                # else fall through to Gemini
        except Exception:
            pass  # fall through

    # --- Attempt 2: Gemini 2.5 Flash audio understanding ---
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        raise HTTPException(status_code=503, detail="No transcription engine available (OpenAI quota exceeded and GEMINI_API_KEY not set)")

    try:
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        # Gemini accepts audio/webm; if not, fall back to audio/ogg
        gem_mime = content_type if content_type.startswith("audio/") else "audio/webm"
        body = {
            "contents": [{
                "parts": [
                    {"inlineData": {"mimeType": gem_mime, "data": b64}},
                    {"text": "Output the words spoken in the audio. If the audio contains no speech, return exactly the single word: empty"}
                ]
            }],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 500},
        }
        # Flash gives much better audio transcription than Flash-Lite
        # (Lite has a tendency to hallucinate "here's the transcript:" loops)
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={gemini_key}",
                json=body,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"Gemini transcribe error: {resp.text[:300]}")
            j = resp.json()
            try:
                text = j["candidates"][0]["content"]["parts"][0]["text"].strip().strip('"').strip()
            except Exception:
                text = ""
            # Treat sentinel as no-speech
            if text.lower() == "empty":
                text = ""
            return {"transcript": text, "language": "auto", "engine": "gemini"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")


@router.get("/debug/openai")
def debug_openai():
    """Debug: test OpenAI connection."""
    import os
    import httpx

    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

    if not api_key:
        return {"status": "error", "reason": "OPENAI_API_KEY not set", "key_length": 0}

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "say hi"}],
                    "max_tokens": 10,
                },
            )
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            return {"status": "ok", "model": model, "response": text, "key_prefix": api_key[:12] + "..."}
        else:
            return {"status": "error", "http_status": resp.status_code, "body": resp.text[:300], "key_prefix": api_key[:12] + "..."}
    except Exception as e:
        return {"status": "error", "exception": str(e), "key_prefix": api_key[:12] + "..."}


@router.get("/debug/kiwoom")
def debug_kiwoom():
    """Gated diagnostic (404 unless DEBUG_KIWOOM=1). Reports creds/token/price +
    outbound IP + Kiwoom's real return_msg. No keys echoed, no user input."""
    import os
    if os.getenv("DEBUG_KIWOOM") != "1":
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not Found")
    import httpx as _hx
    from services import kiwoom_rest
    k, s = kiwoom_rest._creds()
    try:
        egress = _hx.get("https://api.ipify.org", timeout=8.0).text.strip()
    except Exception as e:
        egress = f"(lookup failed: {type(e).__name__})"
    out: dict = {"creds_present": bool(k and s), "server_outbound_ip": egress,
                 "market_open_now": None}
    try:
        from services.assistant_agent import _kr_market_open_now
        out["market_open_now"] = _kr_market_open_now()
    except Exception:
        pass
    if not (k and s):
        out["reason"] = "KIWOOM_APP_KEY/SECRET not visible to this service"
        return out
    probes = []
    for base in ("https://api.kiwoom.com", "https://mockapi.kiwoom.com"):
        rec = {"base": base.replace("https://", "")}
        try:
            r = _hx.post(f"{base}/oauth2/token",
                         json={"grant_type": "client_credentials", "appkey": k, "secretkey": s},
                         headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=15.0)
            rec["http"] = r.status_code
            d = r.json()
            rec["return_code"] = d.get("return_code")
            rec["return_msg"] = str(d.get("return_msg") or "")[:160]
            rec["has_token"] = bool(d.get("token"))
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        probes.append(rec)
    out["token_probe"] = probes
    out["token_ok"] = any(p.get("has_token") for p in probes)
    if out["token_ok"]:
        try:
            q = kiwoom_rest.current_price("005930")
            out["price_ok"] = bool(q and q.get("price"))
            out["sample"] = {"name": (q or {}).get("name"), "price": (q or {}).get("price")}
        except Exception as e:
            out["price_ok"] = False
            out["price_err"] = str(e)[:140]
    return out


class CreateSessionBody(BaseModel):
    user_id: str = Field(default="operator")
    channel: str = Field(default="web", description="web | telegram | api")
    mode: str = Field(default="structured", description="structured | llm")
    title: Optional[str] = None


class UpdateModeBody(BaseModel):
    mode: str = Field(..., description="structured | llm")


class SendMessageBody(BaseModel):
    content: str = Field(..., description="User message text")
    message_type: str = Field(default="plain_text")

    model_config = {"json_schema_extra": {"examples": [
        {"content": "What is the current system status?"},
        {"content": "Run asset summary"},
        {"content": "Show me pending approvals"},
    ]}}


@router.get("/health")
def chat_health():
    """Chat module health check."""
    import os
    ai_enabled = os.getenv("LLM_MODE_ENABLED", os.getenv("AI_ASSIST_ENABLED", "true")).lower() == "true"
    has_key = bool(os.getenv("OPENAI_API_KEY", ""))
    return {
        "module": "chatbot",
        "status": "active",
        "modes": ["structured", "llm"],
        "default_mode": os.getenv("CHAT_DEFAULT_MODE", "structured"),
        "llm_mode_enabled": ai_enabled,
        "openai_configured": has_key,
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
    }


@router.post("/sessions", status_code=201)
def create_session(body: CreateSessionBody, db: Session = Depends(get_db)):
    """Create a new chat session. Returns session with welcome message."""
    return chat_service.create_session(db, body.user_id, body.channel, body.title or "New Chat", body.mode)


@router.patch("/sessions/{session_id}/mode")
def update_mode(session_id: UUID, body: UpdateModeBody, db: Session = Depends(get_db)):
    """Change the chat mode of an existing session."""
    try:
        result = chat_service.update_session_mode(db, session_id, body.mode)
        if not result:
            raise HTTPException(404, "Session not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


class RenameSessionBody(BaseModel):
    title: str = Field(...)


class FolderBody(BaseModel):
    folder: Optional[str] = Field(None, description="Folder name or null to remove from folder")


@router.patch("/sessions/{session_id}/rename")
def rename_session(session_id: UUID, body: RenameSessionBody, db: Session = Depends(get_db)):
    """Rename a chat session."""
    from db.models import ChatSession
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")
    s.title = body.title
    db.commit()
    return {"renamed": True, "id": str(s.id), "title": s.title}


@router.patch("/sessions/{session_id}/folder")
def set_folder(session_id: UUID, body: FolderBody, db: Session = Depends(get_db)):
    """Move session to a folder."""
    from db.models import ChatSession
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")
    s.folder = body.folder
    db.commit()
    return {"folder_set": True, "id": str(s.id), "folder": s.folder}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: UUID, db: Session = Depends(get_db)):
    """Delete a chat session and all its messages."""
    from db.models import ChatSession, ChatMessage
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Session not found")
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    db.delete(s)
    db.commit()
    return {"deleted": True, "id": str(session_id)}


@router.get("/sessions")
def list_sessions(
    user_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List chat sessions, optionally filtered by user."""
    return chat_service.list_sessions(db, user_id=user_id, limit=limit)


@router.get("/sessions/{session_id}")
def get_session(session_id: UUID, db: Session = Depends(get_db)):
    """Get a single chat session."""
    s = chat_service.get_session(db, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


@router.post("/sessions/{session_id}/messages")
def send_message(session_id: UUID, body: SendMessageBody, db: Session = Depends(get_db)):
    """Send a message in a chat session. Returns user message + assistant response."""
    try:
        return chat_service.add_message(
            db=db, session_id=session_id, role="user",
            content=body.content, message_type=body.message_type,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/sessions/{session_id}/messages")
def get_messages(
    session_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get message history for a session."""
    return chat_service.get_messages(db, session_id, limit=limit)


class InterpretBody(BaseModel):
    text: str = Field(..., description="User message to classify")

    model_config = {"json_schema_extra": {"examples": [
        {"text": "show me system status"},
        {"text": "which agents are failing"},
        {"text": "run daily report"},
        {"text": "approve case abc12345"},
        {"text": "why was this rejected"},
    ]}}


class InterpretBatchBody(BaseModel):
    texts: list[str] = Field(...)


@router.post("/interpret")
def interpret_message(body: InterpretBody):
    """Classify a user message into a structured intent with confidence and entities."""
    result = classify(body.text)
    return result.to_dict()


@router.post("/interpret/batch")
def interpret_batch(body: InterpretBatchBody):
    """Classify multiple messages at once. Useful for testing."""
    return classify_batch(body.texts)
