# VIP DESK WATCHDOG (2026-08-11). Checks every 60s that the backend (8000) and the
# dashboard (3000) are listening; restarts whichever died. Runs hidden, forever.
# Started at boot by the Startup .cmd and kept running - the dashboard died silently
# twice on 08-11 and nobody noticed until the boss did.
while ($true) {
  try {
    if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
      Start-Process -FilePath "C:\Users\A\Desktop\VIP\.venv\Scripts\python.exe" `
        -ArgumentList "-m","uvicorn","main:app","--host","127.0.0.1","--port","8000" `
        -WorkingDirectory "C:\Users\A\Desktop\VIP\apps\orchestrator-api" -WindowStyle Hidden
      Start-Sleep -Seconds 45
      try { Invoke-WebRequest "http://127.0.0.1:8000/paper-desk/live/warm" -UseBasicParsing -TimeoutSec 2400 | Out-Null } catch {}
    }
  } catch {}
  try {
    if (-not (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue)) {
      Start-Process -FilePath "cmd.exe" -ArgumentList "/c","npx next start -p 3000" `
        -WorkingDirectory "C:\Users\A\Desktop\VIP\apps\admin-dashboard" -WindowStyle Hidden
    }
  } catch {}
  Start-Sleep -Seconds 60
}
