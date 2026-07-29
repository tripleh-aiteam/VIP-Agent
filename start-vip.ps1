# ============================================================================
#  VIP Agent — one-click local launcher   (boss 2026-07-21)
#  Fixed local URL:  http://localhost:3000
#  Backend API:      http://localhost:8000
#
#  Double-click "Start VIP.lnk" (or right-click this file -> Run with PowerShell)
#  to bring up BOTH servers, each in its own window, running ALL 4 algorithms.
#
#  Keep the two windows OPEN while you use the app. To stop, close the windows.
#  Power tip: Settings -> System -> Power -> Screen & Sleep -> "Never" so the
#  server keeps trading through the day.
# ============================================================================

$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "VIP Agent launcher — repo: $Root" -ForegroundColor Cyan

function Test-Up($url) {
    try { (Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing).StatusCode -ge 200 } catch { $false }
}

# ---- 1) Backend (FastAPI, all 4 algorithms: ONLY_ALGO1=false) --------------
# REPORTS_ENABLED=true: THIS server is the sole morning-report sender (migrated
# off Render 2026-07-29), so it DOES register the report/email jobs. Render must
# have REPORTS_ENABLED=false on its dashboard so the team gets exactly ONE copy.
# Exactly one instance may be 'true' at a time. See SERVER_SETUP.md.
if (Test-Up "http://localhost:8000/health") {
    Write-Host "[backend]  already running on :8000 — skipping" -ForegroundColor Yellow
} else {
    Write-Host "[backend]  starting on http://localhost:8000  (all 4 algorithms, REPORTS_ENABLED=true)" -ForegroundColor Green
    Start-Process powershell -ArgumentList @(
        "-NoExit","-Command",
        "cd '$Root\apps\orchestrator-api'; " +
        "`$env:ONLY_ALGO1='false'; " +
        "`$env:REPORTS_ENABLED='true'; " +
        "Write-Host 'VIP BACKEND :8000 (all 4 algos, reports ON) — keep this window open' -ForegroundColor Cyan; " +
        "python -m uvicorn main:app --host 127.0.0.1 --port 8000"
    )
}

# ---- 2) Dashboard (Next.js, fixed port 3000) -------------------------------
# PRODUCTION mode when a build exists (boss 2026-07-22: dev mode compiled every page
# on first visit = slow). NOTE: after future code changes run `npm run build` in
# apps\admin-dashboard once, then restart — `npm run start` serves the pre-built app.
if (Test-Up "http://localhost:3000") {
    Write-Host "[dashboard] already running on :3000 — skipping" -ForegroundColor Yellow
} else {
    $HasBuild = Test-Path "$Root\apps\admin-dashboard\.next\BUILD_ID"
    $Cmd = if ($HasBuild) { "npm run start" } else { "npm run dev" }
    Write-Host "[dashboard] starting on http://localhost:3000 ($(if ($HasBuild) {'production build'} else {'dev mode - run npm run build for speed'}))" -ForegroundColor Green
    Start-Process powershell -ArgumentList @(
        "-NoExit","-Command",
        "cd '$Root\apps\admin-dashboard'; " +
        "Write-Host 'VIP DASHBOARD :3000 — keep this window open' -ForegroundColor Cyan; " +
        $Cmd
    )
}

# ---- 3) Wait for the dashboard, then open the browser ----------------------
Write-Host "`nWaiting for the dashboard to be ready..." -ForegroundColor Cyan
for ($i = 0; $i -lt 40; $i++) {
    if (Test-Up "http://localhost:3000") {
        Write-Host "READY -> opening http://localhost:3000/testing" -ForegroundColor Green
        Start-Process "http://localhost:3000/testing"
        break
    }
    Start-Sleep -Seconds 2
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  VIP Agent is up:" -ForegroundColor White
Write-Host "    Dashboard : http://localhost:3000" -ForegroundColor White
Write-Host "    Paper Trading (4 algos): http://localhost:3000/testing" -ForegroundColor White
Write-Host "    Backend API : http://localhost:8000/health" -ForegroundColor White
Write-Host "  Two server windows opened — keep them open while using the app." -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan
