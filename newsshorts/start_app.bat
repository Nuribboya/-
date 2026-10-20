@echo off
rem Double-click to run the News Shorts app on Windows.
cd /d "%~dp0\.."
where ffmpeg >nul 2>nul || (echo [!] ffmpeg가 필요합니다. https://www.gyan.dev/ffmpeg/builds/ 에서 설치 후 PATH에 추가하세요. & pause & exit /b 1)
if not exist .venv (
  echo 처음 실행: 필요한 프로그램을 설치합니다...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -q -r newsshorts\requirements.txt
)
.venv\Scripts\python -m newsshorts.app %*
pause
