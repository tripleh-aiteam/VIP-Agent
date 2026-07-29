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

## Reports — who sends the morning emails (`REPORTS_ENABLED`)

Every machine (this server + Render + a dev PC) runs the **full scheduler**. Without a
switch, each one sends the SAME morning reports, so the team once got the recommendation
email **3 times**. The `REPORTS_ENABLED` env var makes report sending controllable per
machine:

- **`REPORTS_ENABLED=false`** — this instance registers **no** outbound report/email jobs
  (Kiwoom 6:30, Newspaper 6:32, YouTube 6:40, Master 6:50, Asset 7:00, Real Estate 7:05,
  Recommendation 7:30, chatbot morning 8:00, the report self-heal, weekly/monthly reports,
  the intraday + paper scorecard emails, breaking-news / story / dip-alert emails, and the
  tournament result email). **Trading, the position guard, call graders, and data
  collectors still run** — only outbound reports are skipped.
- **`REPORTS_ENABLED=true`** (the **default** when the var is absent) — this instance sends
  every report as before.

**Current state (since 2026-07-29):** `REPORTS_ENABLED=true` on this server — **THIS
server is now the sole morning-report sender.** (Render — the previous sender — went
down/`503` during the migration, so from 2026-07-22 to 07-29 *nobody* was sending and the
team got no reports for a week.) It is set in two places (either alone is enough; both
persist across restarts): the repo-root `.env` file, and the backend start command in
`start-vip.ps1` (`$env:REPORTS_ENABLED='true'`).

> ⚠️ **Prevent double-sends:** because this server now defaults-and-is-set to `true`, if
> Render ever comes back it will ALSO send. **Set `REPORTS_ENABLED=false` on Render's
> dashboard** (Dashboard → the `vip-orchestrator` service → **Environment** → add/edit
> `REPORTS_ENABLED=false` → **Save changes**, which redeploys). Exactly **one** instance
> may be `true` at a time.

**Verify after a restart:** the startup log shows the report jobs being registered —
`scheduler: Kiwoom registered (6:30 KST …)`, `… Master registered (6:50 KST …)`,
`… Recommendation report registered (7:30 KST …)`, `… report health check registered
(8:00 / 11:15 / 17:00 KST daily)`, and `… morning-report watchdog registered (8:30 KST
daily)`. If instead you see `reports disabled on this instance (REPORTS_ENABLED=false)`,
the env var is still false somewhere — re-check `.env` and `start-vip.ps1`.

## Auto-start + never-silent net (added 2026-07-29)

- **Auto-start at boot:** `start-vip-service.ps1` is a headless launcher (backend :8000
  with `REPORTS_ENABLED=true` + dashboard :3000, logging to `logs\*.log`, idempotent).
  Run **`register-autostart.ps1` once in an *elevated* PowerShell** to register the
  Scheduled Task `VIP-Agent-AutoStart` (runs at boot **whether or not** anyone is logged
  in) and to persist the never-sleep/hibernate-off power settings. A no-admin fallback
  task `VIP-Agent-AutoStart-Logon` (starts both servers when the server user logs in) is
  already registered. Test outside market hours:
  `Start-ScheduledTask -TaskName VIP-Agent-AutoStart` then `curl http://localhost:8000/health`.
- **Watchdog:** at **08:30 KST daily** the backend checks that today's Kiwoom / Newspaper /
  Master / Asset reports actually landed in the DB; if any are missing it emails the boss
  (`WATCHDOG_ALERT_EMAIL`, default = the SMTP sender) + pings Telegram — so a silent break
  is caught the same morning, not a week later.
- **Keep awake / no sleep:** already `standby-timeout-ac 0` + `hibernate-timeout-ac 0`;
  `register-autostart.ps1` re-applies these + `powercfg /hibernate off` (needs admin).

## Notes

- **Keep the server awake:** Settings → System → Power → Screen/Sleep → **Never**.
- **Engines only trade during KST market hours** (09:00–15:25 Mon–Fri); idle otherwise.
- **Restart after reboot:** just repeat Step 5 (two windows).
- **Update to newer code later:** `cd $HOME\Desktop\VIP-Agent; git pull` then restart Step 5.
- Optional — install Claude Code on the server to keep working with Claude there:
  `npm install -g @anthropic-ai/claude-code`, then run `claude` inside the repo folder.
