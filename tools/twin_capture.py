#!/usr/bin/env python3
"""
Twin Capture — your private "watch & learn" client (Phase 1: multi-source).

Runs on YOUR machine, with YOUR consent. It quietly reads your AI work from
several sources and sends them to your digital twin, which distills them into
reusable knowledge (decisions, patterns, your style). Only YOU can see what your
twin learns — the server enforces a privacy wall, and capture is refused unless
you turned Watch & Learn ON in your Twin → Settings.

Sources it captures (each tagged so your twin knows where it came from):
  1. Claude Code   ~/.claude/projects/**/*.jsonl                  (automatic)
  2. ChatGPT       ~/twin_capture_inbox/chatgpt/   (.txt/.md/.json exports)
  3. Claude Cowork ~/twin_capture_inbox/claude-cowork/
  4. Notion        ~/twin_capture_inbox/notion/    (Markdown/HTML exports)
  5. Google Drive  a folder you point it at (Google Drive for Desktop), via
                   TWIN_GDRIVE_DIR — read-only, files are NEVER moved.
  +  Notes         ~/twin_capture_inbox/           (drop any work text here)

For the inbox folders, drop your exports in and they're sent then archived to a
"_sent" subfolder. For Google Drive, set TWIN_GDRIVE_DIR to your synced Drive
folder — nothing there is moved or modified; it's only read.

No dependencies — just Python 3.

USAGE
  Set 3 env vars (or fill the CONFIG block), then run:
      set TWIN_API=https://vip-orchestrator.onrender.com   (Windows)
      set TWIN_EMAIL=you@tripleh.co.kr
      set TWIN_PASSWORD=your-portal-password
      python twin_capture.py            # capture once
      python twin_capture.py --loop 30  # keep running, every 30 min

  Optional, to also read a synced Google Drive folder:
      set TWIN_GDRIVE_DIR=C:/Users/you/My Drive/Work   (forward slashes are fine)

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

# Make console output safe on Windows (cp1252) terminals — we print ✓ • → etc.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# --- CONFIG (env vars win; otherwise edit these) ---------------------------
API = os.getenv("TWIN_API", "https://vip-orchestrator.onrender.com").rstrip("/")
EMAIL = os.getenv("TWIN_EMAIL", "")
PASSWORD = os.getenv("TWIN_PASSWORD", "")

STATE_FILE = Path.home() / ".twin_capture_state.json"
INBOX = Path.home() / "twin_capture_inbox"
CLAUDE_GLOB = str(Path.home() / ".claude" / "projects" / "**" / "*.jsonl")
MIN_CHARS = 200          # skip trivially short sessions
MAX_CHARS = 24000        # the server distills up to this much per push

# Folder sources: source-key -> (folder path, move_after_send?).
# Inbox subfolders are drop-zones (archived after send). External folders like
# Google Drive are READ-ONLY (never moved) and use mtime de-dup instead.
SOURCE_DIRS = {
    "chatgpt":       (os.getenv("TWIN_CHATGPT_DIR", str(INBOX / "chatgpt")), True),
    "claude-cowork": (os.getenv("TWIN_COWORK_DIR",  str(INBOX / "claude-cowork")), True),
    "notion":        (os.getenv("TWIN_NOTION_DIR",  str(INBOX / "notion")), True),
    "google-drive":  (os.getenv("TWIN_GDRIVE_DIR", ""), False),   # empty = skip
    "notes":         (str(INBOX), True),                          # legacy drop-zone
}


def _post(path: str, body: dict, headers: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def login() -> tuple:
    """Return (email, token, twin_id). Exits on failure."""
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


def _json_to_text(raw: str) -> str:
    """Best-effort flatten of a JSON export (e.g. ChatGPT conversations.json)
    into readable text by collecting string values under common message keys."""
    try:
        data = json.loads(raw)
    except Exception:
        return raw  # not valid JSON — treat as plain text
    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("text", "content", "value", "parts") and isinstance(v, str) and v.strip():
                    out.append(v.strip())
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and len(node) > 1:
            out.append(node.strip())

    walk(data)
    return "\n".join(out)


def _observe(email: str, token: str, twin_id: str, content: str, source: str) -> int:
    headers = {"X-User-Email": email, "X-User-Token": token}
    try:
        d = _post(f"/twins/{twin_id}/observe", {"content": content, "source": source}, headers)
        return int(d.get("learned", 0))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print("   ! capture refused — turn ON Watch & Learn in your Twin → Settings.")
        else:
            print(f"   ! send failed ({source}): HTTP {e.code}")
        return 0
    except Exception as e:
        print(f"   ! send failed ({source}): {e}")
        return 0


def _read_text_file(path: str) -> str:
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if path.lower().endswith(".json"):
        raw = _json_to_text(raw)
    return raw[:MAX_CHARS]


def _capture_folder(email, token, twin_id, folder: str, source: str, move_after: bool, state: dict) -> int:
    """Send new .txt/.md/.json files from a folder, tagged with `source`."""
    learned_total = 0
    d = Path(folder)
    if not folder or not d.exists():
        return 0
    sent = state.setdefault("sent", {})
    files = []
    for ext in ("*.txt", "*.md", "*.json", "*.html"):
        files += glob.glob(str(d / ext))
    for path in files:
        if Path(path).parent.name == "_sent":
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        key = f"{source}::{path}"
        if not move_after and sent.get(key) == mtime:
            continue  # read-only source (Drive) — skip unchanged
        text = _read_text_file(path)
        if len(text.strip()) >= MIN_CHARS:
            learned = _observe(email, token, twin_id, text, source)
            learned_total += learned
            print(f"   • {source}: {os.path.basename(path)} → learned {learned}")
        sent[key] = mtime
        if move_after:
            try:
                done = d / "_sent"
                done.mkdir(exist_ok=True)
                Path(path).rename(done / Path(path).name)
            except Exception:
                pass
    return learned_total


def capture_once():
    email, token, twin_id = login()
    state = _load_state()
    sent = state.setdefault("sent", {})
    total_learned = 0

    # 1) Claude Code sessions — new or changed since last run.
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

    # 2) Folder sources — ChatGPT / Claude Cowork / Notion / Google Drive / Notes.
    INBOX.mkdir(exist_ok=True)
    for source, (folder, move_after) in SOURCE_DIRS.items():
        if folder and not Path(folder).exists() and folder.startswith(str(INBOX)):
            Path(folder).mkdir(parents=True, exist_ok=True)  # create inbox subfolders
        total_learned += _capture_folder(email, token, twin_id, folder, source, move_after, state)

    _save_state(state)
    print(f"   ✓ done — {total_learned} new knowledge item(s) learned this run\n")


def main():
    ap = argparse.ArgumentParser(description="Twin Capture — multi-source watch & learn client")
    ap.add_argument("--loop", type=int, default=0, metavar="MIN",
                    help="keep running, capturing every MIN minutes")
    args = ap.parse_args()

    print(f"Twin Capture → {API}  (as {EMAIL or '<set TWIN_EMAIL>'})")
    print(f"  Inbox: {INBOX}  — drop ChatGPT/Notion/Cowork exports in the matching subfolder")
    gdrive = SOURCE_DIRS['google-drive'][0]
    print(f"  Google Drive: {gdrive or '(not set — set TWIN_GDRIVE_DIR to your synced Drive folder)'}\n")
    if args.loop > 0:
        while True:
            capture_once()
            time.sleep(max(60, args.loop * 60))
    else:
        capture_once()


if __name__ == "__main__":
    main()
