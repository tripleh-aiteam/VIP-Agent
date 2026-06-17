#!/usr/bin/env python3
"""
Twin Capture — your private "watch & learn" client.

Runs on YOUR machine, with YOUR consent. It quietly reads your AI work sessions
and sends them to your digital twin, which distills them into reusable knowledge
(decisions, patterns, your style). Only YOU can see what your twin learns — the
boss cannot (privacy wall on the server).

Sources it captures:
  1. Claude Code sessions   (~/.claude/projects/**/*.jsonl)  — automatic
  2. A paste/inbox folder   (~/twin_capture_inbox/*.txt|*.md) — drop ChatGPT
     exports, notes, or any work text here; it's sent then archived.

Nothing is stored anywhere except your own twin. No dependencies — just Python 3.

USAGE
  Set 3 env vars (or fill the CONFIG block below), then run:
      set TWIN_API=https://vip-orchestrator.onrender.com   (Windows)
      set TWIN_EMAIL=you@tripleh.co.kr
      set TWIN_PASSWORD=your-portal-password
      python twin_capture.py            # capture once
      python twin_capture.py --loop 30  # keep running, every 30 min

It remembers what it already sent (~/.twin_capture_state.json), so re-running
only sends what's new.
"""

import os
import sys
import json
import glob
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path

# --- CONFIG (env vars win; otherwise edit these) ---------------------------
API = os.getenv("TWIN_API", "https://vip-orchestrator.onrender.com").rstrip("/")
EMAIL = os.getenv("TWIN_EMAIL", "")
PASSWORD = os.getenv("TWIN_PASSWORD", "")

STATE_FILE = Path.home() / ".twin_capture_state.json"
INBOX = Path.home() / "twin_capture_inbox"
CLAUDE_GLOB = str(Path.home() / ".claude" / "projects" / "**" / "*.jsonl")
MIN_CHARS = 200          # skip trivially short sessions
MAX_CHARS = 24000        # the server distills up to this much per push


def _post(path: str, body: dict, headers: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def login() -> tuple:
    """Return (email, token). Exits on failure."""
    if not EMAIL or not PASSWORD:
        sys.exit("Set TWIN_EMAIL and TWIN_PASSWORD (env vars or the CONFIG block).")
    try:
        d = _post("/auth/login", {"email": EMAIL, "password": PASSWORD}, {}, timeout=30)
    except urllib.error.HTTPError as e:
        sys.exit(f"Login failed ({e.code}). Check your email/password.")
    except Exception as e:
        sys.exit(f"Cannot reach server: {e}")
    if not d.get("token") or not d.get("twin_id"):
        sys.exit("Login ok but no twin linked to your account. Ask your admin.")
    return EMAIL, d["token"], d["twin_id"]


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sent": {}}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _claude_session_text(path: str) -> str:
    """Flatten a Claude Code .jsonl into readable 'role: text' lines."""
    lines = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                role = ev.get("type") or (ev.get("message") or {}).get("role") or ""
                msg = ev.get("message") or ev
                content = msg.get("content") if isinstance(msg, dict) else None
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                    text = " ".join(p for p in parts if p)
                if text.strip():
                    lines.append(f"{role}: {text.strip()}")
    except Exception:
        return ""
    return "\n".join(lines)[:MAX_CHARS]


def _observe(email: str, token: str, twin_id: str, content: str, source: str) -> int:
    headers = {"X-User-Email": email, "X-User-Token": token}
    try:
        d = _post(f"/twins/{twin_id}/observe", {"content": content, "source": source}, headers)
        return int(d.get("learned", 0))
    except Exception as e:
        print(f"   ! send failed ({source}): {e}")
        return 0


def capture_once():
    email, token, twin_id = login()
    state = _load_state()
    sent = state.setdefault("sent", {})
    total_learned = 0

    # 1) Claude Code sessions — send ones new or changed since last run.
    for path in glob.glob(CLAUDE_GLOB, recursive=True):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        key = f"claude::{path}"
        if sent.get(key) == mtime:
            continue
        text = _claude_session_text(path)
        if len(text) >= MIN_CHARS:
            learned = _observe(email, token, twin_id, text, "claude-code")
            total_learned += learned
            print(f"   • claude-code: {os.path.basename(path)} → learned {learned}")
        sent[key] = mtime

    # 2) Inbox folder — drop ChatGPT exports / notes here.
    INBOX.mkdir(exist_ok=True)
    for path in list(glob.glob(str(INBOX / "*.txt"))) + list(glob.glob(str(INBOX / "*.md"))):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if len(text.strip()) >= MIN_CHARS:
            learned = _observe(email, token, twin_id, text[:MAX_CHARS], "notes")
            total_learned += learned
            print(f"   • inbox: {os.path.basename(path)} → learned {learned}")
        # Archive so it isn't re-sent.
        try:
            done = INBOX / "_sent"
            done.mkdir(exist_ok=True)
            Path(path).rename(done / Path(path).name)
        except Exception:
            pass

    _save_state(state)
    print(f"   ✓ done — {total_learned} new knowledge item(s) learned this run\n")


def main():
    ap = argparse.ArgumentParser(description="Twin Capture — watch & learn client")
    ap.add_argument("--loop", type=int, default=0, metavar="MIN",
                    help="keep running, capturing every MIN minutes")
    args = ap.parse_args()

    print(f"Twin Capture → {API}  (as {EMAIL or '<set TWIN_EMAIL>'})")
    print(f"  Inbox folder: {INBOX}  (drop ChatGPT exports / notes here)\n")
    if args.loop > 0:
        while True:
            capture_once()
            time.sleep(max(60, args.loop * 60))
    else:
        capture_once()


if __name__ == "__main__":
    main()
