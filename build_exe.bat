@echo off
chcp 65001 > nul
echo ============================================
echo  원청 찾기 - exe 만들기
echo ============================================
echo.

python --version
if errorlevel 1 (
    echo [오류] 파이썬이 없습니다. python.org 에서 설치하세요.
    pause
    exit /b 1
)

echo 필요한 것 설치 중...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet requests pyinstaller
if errorlevel 1 (
    echo [오류] 설치 실패. 인터넷 연결을 확인하세요.
    pause
    exit /b 1
)

echo 빌드 중... (2~3분 걸립니다)
python -m PyInstaller --noconfirm --clean prime_finder.spec
if errorlevel 1 (
    echo [오류] 빌드 실패.
    pause
    exit /b 1
)

echo.
echo 완료: dist\PrimeFinder.exe
echo 이 파일 하나만 복사해서 쓰시면 됩니다.
explorer dist
pause
