"""새 버전이 나왔는지 확인한다.

앱이 스스로 자기를 교체하지는 않는다. 실행 중인 exe 가 자기 자신을 덮어쓰는
것은 윈도우에서 까다롭고, 실패하면 앱이 아예 안 켜진다. 서명도 없어 백신이
막을 여지도 있다. 그래서 **알려만 주고 받는 건 사람이** 하도록 했다.

확인에 실패해도 조용히 넘어간다. 업데이트 확인 때문에 앱이 안 켜지면 안 된다.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

REPO = "Nuribboya/-"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

_NUM = re.compile(r"\d+")


@dataclass
class UpdateInfo:
    latest: str            # "1.2.0"
    current: str
    url: str               # 받으러 갈 주소
    notes: str = ""

    @property
    def message(self) -> str:
        return f"새 버전 v{self.latest} 이 나왔습니다 (지금 v{self.current})"


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3', '1.2', 'v1.2.3-beta' 를 비교 가능한 숫자 묶음으로.

    숫자가 하나도 없으면 빈 튜플이라 어떤 버전보다도 작게 취급된다.
    """
    return tuple(int(n) for n in _NUM.findall(text or ""))


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if not a:
        return False                     # 버전을 못 읽으면 업데이트라고 우기지 않는다
    # (1, 2) 와 (1, 2, 0) 이 같게 비교되도록 길이를 맞춘다.
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def check_for_update(current: str, timeout: float = 5.0) -> UpdateInfo | None:
    """최신 릴리스를 확인한다. 새 버전이 없거나 확인에 실패하면 None."""
    try:
        request = urllib.request.Request(
            LATEST_API, headers={"Accept": "application/vnd.github+json",
                                 "User-Agent": f"PrimeFinder/{current}"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:              # 네트워크·차단·형식 무엇이든 조용히 넘어간다
        log.debug("업데이트 확인 실패: %s", exc)
        return None

    tag = str(payload.get("tag_name") or "")
    if not is_newer(tag, current):
        return None
    return UpdateInfo(latest=tag.lstrip("vV"), current=current,
                      url=payload.get("html_url") or RELEASES_PAGE,
                      notes=(payload.get("body") or "").strip())
