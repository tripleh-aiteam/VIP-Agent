# port8000_guard — keep the orchestrator alive (2026-09-02: the server crashed
# twice under concurrent day-replays; tomorrow is the boss's Menu2-vs-Menu3
# demo day and a dead :8000 would blank every desk). Checks each 60s; if the
# port is gone, relaunches uvicorn detached and restores desk-mode both.
while ($true) {
  $up = $false
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/paper-desk/desk-mode" -UseBasicParsing -TimeoutSec 8
    if ($r.StatusCode -eq 200) { $up = $true }
  } catch { $up = $false }
  if (-not $up) {
    $when = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path "C:\Users\A\Desktop\VIP\data_guard_log.txt" -Value "$when :8000 down - relaunching" -Encoding utf8
    Start-Process -FilePath "C:\Users\A\Desktop\VIP\.venv\Scripts\python.exe" `
      -ArgumentList '-m','uvicorn','main:app','--host','127.0.0.1','--port','8000' `
      -WindowStyle Hidden -WorkingDirectory "C:\Users\A\Desktop\VIP\apps\orchestrator-api"
    Start-Sleep -Seconds 90
    try {
      Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/paper-desk/desk-mode?mode=both&force=1" -TimeoutSec 120 | Out-Null
      Add-Content -Path "C:\Users\A\Desktop\VIP\data_guard_log.txt" -Value "$when relaunched + desk both restored" -Encoding utf8
    } catch {}
  }
  Start-Sleep -Seconds 60
}
