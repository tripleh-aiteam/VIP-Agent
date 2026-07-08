@echo off
set B=https://vip-orchestrator.onrender.com/predictions
curl -s -m 60 -X POST "%B%/collector/pass" >nul 2>&1
curl -s -m 90 -X POST "%B%/paper/tick" >nul 2>&1
curl -s -m 60 -X POST "%B%/intraday/bank" >nul 2>&1
curl -s -m 60 -X POST "%B%/chatbot-grade" >nul 2>&1
curl -s -m 90 -X POST "%B%/setups/tick" >nul 2>&1
