@echo off
cd /d %~dp0
set STOCKSCREENER_SP500=1
set STOCKSCREENER_REFRESH_SECONDS=7200
python -m uvicorn stockscreener.web.main:app --host 0.0.0.0 --port 8000
pause
