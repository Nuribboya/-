"""PyInstaller로 단일 실행 파일(Windows: dist/YouTubeMonitor.exe)을 만든다.

    pip install -r requirements.txt pyinstaller
    python build_exe.py              # 창 모드(GUI) exe
    python build_exe.py --console    # 디버깅용: 콘솔 창이 함께 뜨는 exe

- --onefile 로 exe 하나만 만든다. prompts/ 기본 템플릿은 exe 안에 넣고, 첫 실행 때 exe 옆에
  복사한다(이후엔 exe 옆 파일을 읽으므로 메모장으로 고쳐 쓰면 된다).
- config.yaml 은 exe에 넣지 않는다. 첫 실행 때 설정 창에서 입력받아 exe 옆에 만든다.
- Ollama와 ffmpeg는 포함하지 않는다 (사용자가 따로 설치). ffmpeg.exe 를 exe 옆에 두면 그걸 사용한다.
- PyInstaller는 크로스 컴파일을 하지 않으므로 Windows용 exe는 Windows에서 빌드해야 한다.
  (.github/workflows/yt-monitor-exe.yml 이 GitHub의 Windows 서버에서 대신 빌드해 준다)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import PyInstaller.__main__

HERE = Path(__file__).resolve().parent


def build(console: bool = False) -> Path:
    sep = os.pathsep
    PyInstaller.__main__.run([
        str(HERE / "main.py"),
        "--name", "YouTubeMonitor",
        "--onefile",
        "--console" if console else "--windowed",
        "--noconfirm",
        "--clean",
        "--paths", str(HERE.parent),                     # yt_monitor 패키지 import 경로
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        "--add-data", f"{HERE / 'prompts'}{sep}prompts",  # 기본 프롬프트 템플릿
        "--collect-submodules", "yt_monitor",
        "--collect-data", "googleapiclient",             # YouTube API discovery 문서(JSON)
        "--collect-data", "tzdata",                      # Windows용 시간대 DB (Asia/Seoul)
        "--copy-metadata", "APScheduler",
        # 영상 생성: edge-tts(aiohttp 웹소켓) + 인증서 번들
        "--collect-submodules", "edge_tts",
        "--collect-data", "certifi",
        # exe 용량을 줄이기 위해 쓰지 않는 무거운 패키지 제외
        "--exclude-module", "sympy",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        "--exclude-module", "IPython",
        "--exclude-module", "pytest",
    ])
    exe = HERE / "dist" / ("YouTubeMonitor.exe" if sys.platform == "win32" else "YouTubeMonitor")
    print(f"\n완료: {exe}")
    return exe


if __name__ == "__main__":
    build(console="--console" in sys.argv)
