# Twin Capture — your private "watch & learn" client

A tiny tool (one Python file, no installs) that lets your **digital twin learn
from your real AI work** automatically. It reads your AI sessions on **your**
machine and sends them to **your** twin, which distills them into reusable
knowledge. **Only you can see what your twin learns — your boss cannot** (the
server enforces a privacy wall).

## What it captures
1. **Claude Code sessions** — `~/.claude/projects/**/*.jsonl` (automatic)
2. **An inbox folder** — `~/twin_capture_inbox/` — drop **ChatGPT exports**,
   notes, or any work text (`.txt` / `.md`) here; it's sent, then archived.

## Setup (2 minutes)
1. Make sure you have **Python 3** (`python --version`).
2. Download `twin_capture.py`.
3. Set 3 values (your portal login):

   **Windows (Command Prompt):**
   ```
   set TWIN_API=https://vip-orchestrator.onrender.com
   set TWIN_EMAIL=you@tripleh.co.kr
   set TWIN_PASSWORD=your-portal-password
   python twin_capture.py
   ```

   **Mac/Linux:**
   ```
   export TWIN_API=https://vip-orchestrator.onrender.com
   export TWIN_EMAIL=you@tripleh.co.kr
   export TWIN_PASSWORD=your-portal-password
   python3 twin_capture.py
   ```

## Run modes
- `python twin_capture.py` — capture once (sends anything new).
- `python twin_capture.py --loop 30` — keep running, capture every 30 minutes.

It remembers what it already sent (`~/.twin_capture_state.json`), so re-running
only sends new material — safe to run as often as you like.

## To feed a ChatGPT chat (or any text)
1. Copy the conversation (or export it).
2. Save it as a `.txt` or `.md` file inside `~/twin_capture_inbox/`.
3. Run the tool — it sends it to your twin and moves the file to `_sent/`.

## Privacy
- Captured material goes **only to your own twin** and is private to you.
- Run it only on material you're comfortable your twin learning from.
- Stop anytime — just don't run it. Nothing runs in the background unless you
  start it with `--loop`.
