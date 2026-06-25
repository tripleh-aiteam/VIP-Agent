@echo off
REM Daily ML retrain launcher — pulls latest prices, rebuilds features, retrains the
REM per-stock models, and writes fresh predictions. Crash-proof on Korean (cp949)
REM consoles via UTF-8. Schedule this once with Windows Task Scheduler at 16:30 KST:
REM
REM   schtasks /Create /TN "VIP_DailyRetrain" /TR "\"%~f0\"" /SC DAILY /ST 16:30 /F
REM
REM Logs append to ml\retrain.log next to this file.
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo ==== retrain start %DATE% %TIME% ==== >> "%~dp0retrain.log"
python ml\daily_run.py >> "%~dp0retrain.log" 2>&1
echo ==== retrain end   %DATE% %TIME% (exit %ERRORLEVEL%) ==== >> "%~dp0retrain.log"
