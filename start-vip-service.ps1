# =====================================================================
#  VIP Agent — headless auto-start launcher   (Scheduled Task target)
#  Brings BOTH servers back after a reboot / power-blip WITHOUT needing a
#  logged-in user or an open window:
#    - Backend  :8000  (all algorithms: ONLY_ALGO1=false, REPORTS_ENABLED=true)
#    - Dashboard:3000  (Next.js production build)
#  THIS server is now the sole morning-report sender (migrated off Render
#  2026-07-29) — hence REPORTS_ENABLED=true. Render must be set false.
#  Idempotent: skips whichever server is already listening.
#  Logs (fixes the old stdout-only gap): <repo>\logs\backend.*.log etc.
#  Registered by register-autostart.ps1 (run once, elevated).
# =====================================================================
$ErrorActionPreference = "SilentlyContinue"
$Root = "C:\Users\A\Desktop\VIP"
$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

# Put Node (nvm4w) on PATH so the dashboard's npm/node resolve even when the
# task runs in a bare Task-Scheduler environment (no user profile PATH).
$NodeDir = "C:\nvm4w\nodejs"
if (Test-Path $NodeDir) { $env:PATH = "$NodeDir;$env:PATH" }

function Test-Up($url) {
    try { (Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing).StatusCode -ge 200 } catch { $false }
}

# ---- 1) Backend (FastAPI, all algorithms, reports ON) --------------------
$env:ONLY_ALGO1      = "false"
$env:REPORTS_ENABLED = "true"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
if (-not (Test-Up "http://localhost:8000/health")) {
    Start-Process -FilePath $Py `
        -ArgumentList @("-m","uvicorn","main:app","--host","127.0.0.1","--port","8000") `
        -WorkingDirectory (Join-Path $Root "apps\orchestrator-api") `
        -RedirectStandardOutput (Join-Path $Logs "backend.out.log") `
        -RedirectStandardError  (Join-Path $Logs "backend.err.log") `
        -WindowStyle Hidden
}

# ---- 2) Dashboard (Next.js, port 3000, production build) -----------------
if (-not (Test-Up "http://localhost:3000")) {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-Command",
                        "Set-Location '$Root\apps\admin-dashboard'; npm run start") `
        -RedirectStandardOutput (Join-Path $Logs "dashboard.out.log") `
        -RedirectStandardError  (Join-Path $Logs "dashboard.err.log") `
        -WindowStyle Hidden
}
