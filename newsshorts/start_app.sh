#!/usr/bin/env bash
# Run the News Shorts app on macOS/Linux: ./newsshorts/start_app.sh  (add --lan for phone access)
set -e
cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null || { echo "[!] ffmpeg가 필요합니다. macOS: brew install ffmpeg / Ubuntu: sudo apt install ffmpeg"; exit 1; }
if [ ! -d .venv ]; then
  echo "처음 실행: 필요한 프로그램을 설치합니다..."
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r newsshorts/requirements.txt
fi
exec .venv/bin/python -m newsshorts.app "$@"
