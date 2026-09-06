@echo off
REM 재고 관리 웹 앱을 실행하고 브라우저를 여는 편의 스크립트.
REM 이 파일을 더블클릭하면 됩니다.

cd /d "%~dp0\.."
start "" http://localhost:8001
python -m uvicorn inventory.webapp.main:app --reload --port 8001
pause
