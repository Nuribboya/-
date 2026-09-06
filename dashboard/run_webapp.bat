@echo off
REM 통합 대시보드(원청 레이더 + 재고 관리)를 실행하고 브라우저를 여는 편의 스크립트.
REM 이 파일을 더블클릭하면 됩니다 (필요한 환경변수가 미리 설정되어 있어야 합니다).

cd /d "%~dp0\.."
start "" http://localhost:8000
python -m uvicorn dashboard.main:app --reload
pause
