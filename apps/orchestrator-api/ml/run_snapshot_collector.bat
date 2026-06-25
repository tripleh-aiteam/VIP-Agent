@echo off
REM Live-data snapshot collector launcher — runs CONTINUOUSLY: polls Kiwoom 실전 호가/
REM 수급/program/공매도 for the universe and upserts the realtime_snapshot table that the
REM website (Render) reads. Idles when the market is closed; keeps running 24/7.
REM Register once with Task Scheduler (At log on + auto-restart) — see commands in chat.
REM Logs append to ml\snapshot.log next to this file.
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo ==== collector start %DATE% %TIME% ==== >> "%~dp0snapshot.log"
python ml\realtime\rt_snapshot_collector.py >> "%~dp0snapshot.log" 2>&1
echo ==== collector exited %DATE% %TIME% (exit %ERRORLEVEL%) ==== >> "%~dp0snapshot.log"
