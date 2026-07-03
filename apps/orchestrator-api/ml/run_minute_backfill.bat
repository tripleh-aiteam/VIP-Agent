@echo off
cd /d "%~dp0.."
python ml\backfill_minute_hist.py >> ml\backfill.log 2>&1
