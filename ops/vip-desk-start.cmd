@echo off
rem VIP DESK AUTO-START (2026-08-11). Lives in the Startup folder because schtasks is
rem access-denied on this PC - this is the only no-admin way to survive a reboot.
rem Boots the backend (collector + rules), the dashboard, then warms the ML models.
rem Safe to run twice: each service checks its own port before starting.

powershell -NoProfile -WindowStyle Hidden -Command ^
  "if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) { Start-Process -FilePath 'C:\Users\A\Desktop\VIP\.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory 'C:\Users\A\Desktop\VIP\apps\orchestrator-api' -WindowStyle Hidden }"

powershell -NoProfile -WindowStyle Hidden -Command ^
  "if (-not (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue)) { Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npx next start -p 3000' -WorkingDirectory 'C:\Users\A\Desktop\VIP\apps\admin-dashboard' -WindowStyle Hidden }"

rem give the backend a minute to bind, then warm the models and fetch the overnight US
powershell -NoProfile -WindowStyle Hidden -Command ^
  "Start-Sleep -Seconds 60; try { Invoke-WebRequest 'http://127.0.0.1:8000/paper-desk/overnight' -UseBasicParsing -TimeoutSec 60 | Out-Null } catch {}; try { Invoke-WebRequest 'http://127.0.0.1:8000/paper-desk/live/warm' -UseBasicParsing -TimeoutSec 2400 | Out-Null } catch {}"

rem persistent watchdog: restarts either service if it dies; the marker file guard
rem plus the script's own single loop keep this harmless to run twice
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\A\Desktop\VIP\vip-desk-watchdog.ps1"
