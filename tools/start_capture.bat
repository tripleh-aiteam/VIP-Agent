@echo off
REM ============================================================
REM  Twin Capture - one-click launcher (Windows)
REM  Double-click this file to feed your twin from your 5 sources.
REM ============================================================
setlocal
cd /d "%~dp0"

set "TWIN_API=https://vip-orchestrator.onrender.com"

echo.
echo  ===  Twin Capture  ===
echo  This sends your AI work to your twin so it learns.
echo  (Watch and Learn must be ON in your Twin - Settings.)
echo.

set /p TWIN_EMAIL="  Your twin login email: "
set /p TWIN_PASSWORD="  Your password: "

echo.
echo  Optional - point at a synced Google Drive folder (or just press Enter to skip):
set /p TWIN_GDRIVE_DIR="  Google Drive folder path: "

echo.
echo  How do you want to run it?
echo     [1] Once now  (capture and stop - good for a quick test)
echo     [2] Keep running every 30 min  (background learning)
echo.
set /p MODE="  Choose 1 or 2: "

echo.
if "%MODE%"=="2" (
  echo  Running continuously - leave this window open. Press Ctrl+C to stop.
  python twin_capture.py --loop 30
) else (
  python twin_capture.py
)

echo.
echo  Done. Refresh your portal to see the numbers climb.
pause
endlocal
