# 🖥️ Server Setup — Run the 3-Algorithm Trading Engine on the Server PC

Goal: run the whole paper-trading platform (backend + dashboard, all 3 algorithms)
on the server PC (DESKTOP-UUI4DM9main), 24/7, instead of a personal PC or Render.

All trading data lives in **Supabase** (shared database) — so the server continues the
same test with the same history. Nothing is lost by moving machines.

---

## Step 1 — Install prerequisites (skip what's already installed)

Open **PowerShell** on the server and check:

```powershell
git --version        # need any recent version
python --version     # need 3.11.x
node --version       # need 18+
```

If missing, install (winget comes with Windows 11):

```powershell
winget install Git.Git
winget install Python.Python.3.11
winget install OpenJS.NodeJS.LTS
```

Close and reopen PowerShell after installing so PATH updates.

## Step 2 — Clone the code

```powershell
cd $HOME\Desktop
git clone https://github.com/tripleh-aiteam/VIP-Agent.git
cd VIP-Agent
```

(If it asks to log in: use the GitHub account in the browser window it opens.)

## Step 3 — Copy the 2 secret files (NOT in GitHub, on purpose)

On the **old PC**, open these two files in Notepad:
- `Desktop\VIP Agent\vip-ai-platform\.env`
- `Desktop\VIP Agent\vip-ai-platform\.env.supabase`

Select-all → copy, then in the **remote-desktop window** paste into new Notepad files
on the server, saved with the SAME names in the cloned repo root:
- `Desktop\VIP-Agent\.env`
- `Desktop\VIP-Agent\.env.supabase`

(Chrome Remote Desktop syncs the clipboard between the two PCs, so copy → paste works.
In Notepad "Save as", set type to "All files" so it doesn't add `.txt`.)

Then create the dashboard config `Desktop\VIP-Agent\apps\admin-dashboard\.env.local`:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_USE_AGENT_ENDPOINT=true
NEXT_PUBLIC_SHOW_ALGO_23=true
```

## Step 4 — Install dependencies (one time, ~5-10 min)

```powershell
cd $HOME\Desktop\VIP-Agent
pip install -r apps\orchestrator-api\requirements.txt
cd apps\admin-dashboard
npm install
```

## Step 5 — Run it (2 PowerShell windows)

**Window 1 — backend (all 3 algorithms):**
```powershell
cd $HOME\Desktop\VIP-Agent\apps\orchestrator-api
$env:ONLY_ALGO1 = "false"     # false = run ALL 3 algorithms (Render keeps true)
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```
Wait for "Application startup complete".

**Window 2 — dashboard:**
```powershell
cd $HOME\Desktop\VIP-Agent\apps\admin-dashboard
npm run dev
```

Open **http://localhost:3000/testing** in the server's browser →
all 3 algorithm pages, verdict board, live trading.

## Step 6 — Checks

- `http://localhost:8000/health` → should answer fast (~0.1s)
- Algorithm pages → toggle each algorithm **ON** (Auto) if off
- The 🏁 verdict board shows the SAME history as before (same database)

## Notes

- **Keep the server awake:** Settings → System → Power → Screen/Sleep → **Never**.
- **Engines only trade during KST market hours** (09:00–15:25 Mon–Fri); idle otherwise.
- **Restart after reboot:** just repeat Step 5 (two windows).
- **Update to newer code later:** `cd $HOME\Desktop\VIP-Agent; git pull` then restart Step 5.
- Optional — install Claude Code on the server to keep working with Claude there:
  `npm install -g @anthropic-ai/claude-code`, then run `claude` inside the repo folder.
