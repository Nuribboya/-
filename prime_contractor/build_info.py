"""지금 돌고 있는 게 어느 빌드인지.

베타는 번호가 없으니 '언제, 어느 코드로 만든 건지'로 구분한다. 빌드할 때
_build.py 가 만들어져 날짜와 커밋이 들어간다. 소스에서 바로 돌리면 없다.
"""
from __future__ import annotations

from prime_contractor import CHANNEL, __version__

try:
    from prime_contractor._build import BUILD  # type: ignore[attr-defined]
except ImportError:          # 소스에서 바로 실행 중
    BUILD = ""

IS_BETA = CHANNEL == "beta"


def label() -> str:
    """제목줄에 쓰는 이름. 베타는 '베타 · 09-23 a1b2c3d', 정식은 'v1.0.0'."""
    if IS_BETA:
        return f"베타 · {BUILD}" if BUILD else "베타 · 개발용"
    return f"v{__version__}"


def should_check_updates() -> bool:
    """베타는 계속 바뀌니 켤 때마다 '새 버전' 알림을 띄우지 않는다."""
    return not IS_BETA
