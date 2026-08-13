# Moving the desk to a new server

The whole path, in order. Code travels through GitHub; secrets and the sacred
trade data travel as files you carry yourself (never through GitHub).

## 0. When to move

**After 15:30 (market close), never mid-session.** A restart mid-day loses live
tape that can never be recovered. Keep the old PC running until the new server
has passed the checks in step 8.

## 1. Push the code (old PC — the boss does the push)

Everything is already committed locally. In a terminal at `C:\Users\A\Desktop\VIP`:

```
git push origin main
```

(Repo: https://github.com/tripleh-aiteam/VIP-Agent — push from your own GitHub
login; this machine's automation cannot push, by design.)

## 2. Pack the two things GitHub must never carry (old PC)

Run AFTER the market closes, so the day's tape is complete:

```powershell
Compress-Archive -Path C:\Users\A\Desktop\VIP\apps\orchestrator-api\data `
  -DestinationPath C:\Users\A\Desktop\vip-data.zip -Force
Copy-Item C:\Users\A\Desktop\VIP\.env          C:\Users\A\Desktop\vip-env.txt
Copy-Item C:\Users\A\Desktop\VIP\.env.supabase C:\Users\A\Desktop\vip-env-supabase.txt
```

Carry `vip-data.zip` + the two env files by USB or a private drive.
`vip-data.zip` holds every trade record, order-book snapshot, and the 250-day
1-minute history (~330MB). The env files hold the database URL and the Kiwoom
keys — treat them like passwords.

## 3. New server — install the base tools (once)

- **Python 3.11+** (check `python --version`)
- **Node.js 18+** (check `node --version`)
- **Git**

## 4. Clone and build

```powershell
cd C:\Users\<YOU>\Desktop
git clone https://github.com/tripleh-aiteam/VIP-Agent.git VIP
cd VIP
python -m venv .venv
.venv\Scripts\pip install -r apps\orchestrator-api\requirements.txt
cd apps\admin-dashboard
npm install
npm run build
```

## 5. Put the carried files in place

- `vip-env.txt`          → save as `C:\...\VIP\.env`
- `vip-env-supabase.txt` → save as `C:\...\VIP\.env.supabase`
- unzip `vip-data.zip`   → so it becomes `C:\...\VIP\apps\orchestrator-api\data\...`
  (the folder must contain `kiwoom_tape\`, `minute1_hist\`, `overnight.json` etc.)

## 6. Kiwoom API key

The keys live in `.env` as `KIWOOM_APP_KEY` / `KIWOOM_APP_SECRET`
(`KIWOOM_MOCK` picks the mock/real server). The copied `.env` already carries
the current key, so normally there is nothing to do. Get a NEW key only if you
want this server to have its own:

1. Log in at the Kiwoom Open API portal (openapi.kiwoom.com) with the trading
   account's ID.
2. My Page → app registration (앱 등록) → issue APP KEY + APP SECRET.
3. Paste both into `.env` on the new server.

**One server at a time**: two servers using the same key at once fight over the
access token and both start failing. Run the collector on only one machine.

## 7. Autostart + watchdog

The two ops files are in the repo under `ops\`:

1. Open `ops\vip-desk-start.cmd` and `ops\vip-desk-watchdog.ps1` and fix the
   paths (they say `C:\Users\A\...` — change to the new server's user).
2. Copy `vip-desk-start.cmd` into the Startup folder
   (`Win+R` → `shell:startup`) so a reboot brings the whole desk back:
   backend :8000 → dashboard :3000 → model warm → US overnight → watchdog.
3. Reboot once and let it start everything by itself — that is the test.

## 8. Prove it before trusting it

```powershell
# backend answers
Invoke-WebRequest http://127.0.0.1:8000/paper-desk/live/status
# board loads
start http://127.0.0.1:3000/testing/live
```

- The day dropdown must show the stored days with their records (that proves
  the data zip landed in the right place).
- Click a past trade → the chart must draw with buy/sell arrows.
- Next morning at 09:00 confirm new bars appear (that proves the Kiwoom key
  and the collector work).

## 9. Retire the old PC's duties (important)

- Old PC: set `REPORTS_ENABLED=false` in `.env` (only ONE machine may send the
  morning reports), and remove `vip-desk-start.cmd` from its Startup folder.
- New PC: `REPORTS_ENABLED=true` if it takes over the morning reports.
- Keep the old PC's `data\` folder as a backup — it is the only other copy of
  the trade history.

## Known traps from this desk's history

- Something else may already be sitting on port 8000 on a new machine (on the
  old server it was the "JONSBO PC Monitor" app) — if the backend won't bind,
  check `Get-NetTCPConnection -LocalPort 8000` first.
- After any backend start, the first rules call is slow until the warm
  finishes (the .cmd does this automatically).
- The dashboard must be `npm run build` + `next start` (production), not
  `npm run dev`.
