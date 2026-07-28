@echo off
title VIP Agent Launcher
echo Starting VIP Agent (backend + dashboard)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-vip.ps1"
echo.
echo If two server windows opened, VIP is running. Keep them open.
echo (This window can be closed.)
pause
