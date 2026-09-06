@echo off
REM leadradar 웹 앱을 실행하고 브라우저를 여는 편의 스크립트.
REM 이 파일을 더블클릭하면 됩니다 (ANTHROPIC_API_KEY, DART_API_KEY 환경변수가
REM 미리 설정되어 있어야 합니다).

cd /d "%~dp0\.."
start "" http://localhost:8000
python -m uvicorn leadradar.webapp.main:app --reload
pause
