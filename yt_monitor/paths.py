"""실행 위치 관련 경로 처리 (PyInstaller --onefile 대응).

--onefile exe는 실행할 때마다 임시 폴더(sys._MEIPASS)에 풀린다. 그래서
- 사용자가 고치는 파일(config.yaml, prompts/, data/, outputs/ ...)은 exe가 있는 폴더(app_dir)에 두고
- exe 안에 넣어 둔 기본 파일(prompts 기본 템플릿 등)은 resource_dir에서 읽는다.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
PACKAGE_DIR = Path(__file__).resolve().parent


def app_dir() -> Path:
    """사용자 파일이 놓이는 폴더. exe면 exe 옆, 소스 실행이면 yt_monitor/ 폴더."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return PACKAGE_DIR


def resource_dir() -> Path:
    """exe에 번들된 기본 리소스 폴더."""
    return Path(getattr(sys, "_MEIPASS", PACKAGE_DIR))


def default_config_path() -> Path:
    return app_dir() / "config.yaml"


def ensure_user_copy(relative: str, base: Path | None = None) -> Path:
    """base(기본 app_dir)에 relative 파일/폴더가 없으면 번들 기본본을 복사해 두고 경로를 반환.

    이미 있으면 사용자가 수정한 것으로 보고 건드리지 않는다. 폴더면 빠진 파일만 채운다.
    """
    base = base or app_dir()
    target = base / relative
    source = resource_dir() / relative
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for f in source.rglob("*"):
            dest = target / f.relative_to(source)
            if f.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            elif not dest.exists():
                shutil.copyfile(f, dest)
    elif source.is_file() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return target
