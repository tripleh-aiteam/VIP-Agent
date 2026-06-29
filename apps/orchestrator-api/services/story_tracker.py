"""story_tracker.py — track a major news STORY over time + executive reporting.

Pins a topic (e.g. the '3 Mega Investment Projects'), searches for follow-up news each
day, dedups, and generates a CONCISE EXECUTIVE BRIEF for management — and emails the new
developments. Built on the existing web_search (Serper) + LLM + report-email.

Tables:
  tracked_stories(key, topic, queries[], tickers[], active, created_at)
  story_updates(story_key, url, title, source, ts)  -- one row per unique article
"""
from __future__ import annotations

from typing import Any, Optional

from services.logger import log


def ensure_tables(db) -> None:
    from sqlalchemy import text
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS tracked_stories (
            key        TEXT PRIMARY KEY,
            topic      TEXT NOT NULL,
            queries    TEXT[] NOT NULL,
            tickers    TEXT[] DEFAULT '{}',
            active     BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT now()
        )"""))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS story_updates (
            story_key TEXT NOT NULL,
            url       TEXT NOT NULL,
            title     TEXT,
            source    TEXT,
            ts        TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (story_key, url)
        )"""))
    db.commit()


def seed_story(db, key: str, topic: str, queries: list[str], tickers: list[str] | None = None) -> dict:
    from sqlalchemy import text
    ensure_tables(db)
    db.execute(text(
        "INSERT INTO tracked_stories (key, topic, queries, tickers, active) "
        "VALUES (:k,:t,:q,:ti,TRUE) ON CONFLICT (key) DO UPDATE SET "
        "topic=EXCLUDED.topic, queries=EXCLUDED.queries, tickers=EXCLUDED.tickers, active=TRUE"),
        {"k": key, "t": topic, "q": queries, "ti": tickers or []})
    db.commit()
    return {"ok": True, "key": key, "topic": topic, "queries": len(queries)}


_STORY_KW = {
    "mega_projects_2026": ("클러스터", "데이터센터", "메가", "투자", "인프라", "반도체 단지",
                           "cluster", "data center", "investment", "infrastructure"),
}


def update_story(db, key: str, per_query: int = 5) -> dict:
    """Store NEW (unseen-URL) articles from (a) the existing raw_news collection for the
    story's tickers/keywords and (b) a fresh web search. Returns the new items."""
    from sqlalchemy import text
    from services.web_search import search_web
    ensure_tables(db)
    row = db.execute(text("SELECT queries, tickers FROM tracked_stories WHERE key=:k"), {"k": key}).first()
    if not row:
        return {"ok": False, "error": f"no tracked story '{key}'"}
    new = []

    # (a) existing raw_news for the tickers, filtered to the story's keywords
    kws = _STORY_KW.get(key, ())
    try:
        rn = db.execute(text(
            "SELECT title, url, source FROM raw_news WHERE ticker = ANY(:t) "
            "AND ts > now() - interval '10 days' ORDER BY ts DESC LIMIT 80"),
            {"t": list(row[1] or [])}).fetchall()
        for r in rn:
            title = r[0] or ""
            if kws and not any(k in title for k in kws):
                continue
            url = (r[1] or "").strip()
            if not url:
                continue
            ins = db.execute(text(
                "INSERT INTO story_updates (story_key, url, title, source) VALUES (:k,:u,:t,:s) "
                "ON CONFLICT (story_key, url) DO NOTHING RETURNING url"),
                {"k": key, "u": url, "t": title[:300], "s": r[2] or "raw_news"}).first()
            if ins:
                new.append({"title": title, "url": url, "source": r[2]})
    except Exception as e:
        log.warning(f"story raw_news scan: {str(e)[:80]}")

    # (b) fresh web search (Serper — works where SERPER_API_KEY is set, e.g. Render)
    for q in (row[0] or []):
        try:
            res = search_web(q, num_results=per_query, recency="week")
        except Exception as e:
            log.warning(f"story search '{q}': {str(e)[:80]}")
            continue
        for it in (res.get("results") or []):
            url = (it.get("link") or it.get("url") or "").strip()
            if not url:
                continue
            ins = db.execute(text(
                "INSERT INTO story_updates (story_key, url, title, source) VALUES (:k,:u,:t,:s) "
                "ON CONFLICT (story_key, url) DO NOTHING RETURNING url"),
                {"k": key, "u": url, "t": (it.get("title") or "")[:300], "s": it.get("source") or "web"}).first()
            if ins:
                new.append({"title": it.get("title"), "url": url, "source": it.get("source")})
    db.commit()
    return {"ok": True, "key": key, "new": len(new), "items": new}


def executive_brief(db, key: str, lang: str = "ko", model: Optional[str] = None) -> dict:
    """LLM executive summary of the story's accumulated headlines."""
    from sqlalchemy import text
    from services.llm_client import chat_completion_sync
    ensure_tables(db)
    s = db.execute(text("SELECT topic, tickers FROM tracked_stories WHERE key=:k"), {"k": key}).first()
    if not s:
        return {"ok": False, "error": f"no tracked story '{key}'"}
    rows = db.execute(text(
        "SELECT title, source, ts FROM story_updates WHERE story_key=:k ORDER BY ts DESC LIMIT 40"),
        {"k": key}).fetchall()
    if not rows:
        return {"ok": False, "error": "no updates yet — run update_story first"}
    en = str(lang or "").lower().startswith("en")
    headlines = "\n".join(f"- {r[0]} ({r[1]})" for r in rows if r[0])
    sys = ("You are an equity research analyst writing a CONCISE EXECUTIVE BRIEF for management. "
           "From the headlines, produce: (1) one-line status, (2) 3-5 key developments as bullets, "
           "(3) market/stock impact (esp. the named tickers), (4) what to watch next. Be factual, "
           "no fluff. " + ("Write in English." if en else "한국어로 작성."))
    usr = f"TOPIC: {s[0]}\nTICKERS: {', '.join(s[1] or [])}\n\nHEADLINES (newest first):\n{headlines[:6000]}"
    try:
        brief = chat_completion_sync(sys, [{"role": "user", "content": usr}], model=model)
    except Exception as e:
        return {"ok": False, "error": f"LLM failed: {str(e)[:100]}"}
    return {"ok": True, "key": key, "topic": s[0], "brief": brief, "headline_count": len(rows)}


def daily_monitor(db, email: bool = True, recipients: Optional[list[str]] = None) -> dict:
    """Update every active story; for those with NEW developments, email an exec brief."""
    from sqlalchemy import text
    ensure_tables(db)
    keys = [r[0] for r in db.execute(text("SELECT key FROM tracked_stories WHERE active")).fetchall()]
    out = []
    for k in keys:
        up = update_story(db, k)
        if up.get("new", 0) > 0 and email:
            br = executive_brief(db, k, lang="ko")
            if br.get("ok"):
                try:
                    from services.report_email import default_recipients, send_plain_email
                    tos = recipients or default_recipients()
                    subj = f"[속보 추적] {br['topic']} — 신규 {up['new']}건"
                    for to in tos:
                        send_plain_email(to, subj, br["brief"])
                    out.append({"key": k, "new": up["new"], "emailed": len(tos)})
                except Exception as e:
                    log.warning(f"story email {k}: {str(e)[:80]}")
        else:
            out.append({"key": k, "new": up.get("new", 0), "emailed": 0})
    return {"ok": True, "stories": out}


# The user's PRIORITY topic — seeded on first import/use.
MEGA_PROJECTS = {
    "key": "mega_projects_2026",
    "topic": "3대 메가 투자 프로젝트 (반도체 클러스터 · 피지컬 AI 인프라 · 데이터센터 클러스터) — 정부·삼성·SK하이닉스",
    "queries": [
        "반도체 클러스터 투자 발표 정부 삼성 SK하이닉스",
        "데이터센터 클러스터 투자 한국",
        "physical AI infrastructure Korea investment",
        "3대 메가 투자 프로젝트 발표",
        "Samsung SK hynix government semiconductor investment cluster",
    ],
    "tickers": ["005930", "000660"],
}
