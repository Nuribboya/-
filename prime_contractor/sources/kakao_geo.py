"""카카오 로컬 API 로 주소 → 좌표. 공장마다 번지 단위로 거리를 재려고 쓴다.

    GET https://dapi.kakao.com/v2/local/search/address.json?query=주소
    Authorization: KakaoAK {REST API 키}

키는 developers.kakao.com → 내 애플리케이션 → 앱 키 → 'REST API 키'. 앱 설정에서
'카카오맵' 사용을 켜야 한다. 한 번 찾은 주소는 디스크에 남겨 다시 묻지 않는다.
키가 없거나 못 찾은 주소는 읍·면·동 중심점(geo.py)으로 잰다.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

import requests

log = logging.getLogger(__name__)

URL = "https://dapi.kakao.com/v2/local/search/address.json"


class KakaoKeyError(RuntimeError):
    pass


def clean_address(address: str) -> str:
    """'(총 2 필지)', '(주)OO공장', 층·호 같은 꼬리를 떼어 지번·도로명만 남긴다."""
    text = re.sub(r"\([^)]*\)", " ", address or "")
    text = text.split(",")[0]
    text = re.sub(r"\s+(지하)?\d+층.*$|\s+[A-Za-z가-힣]*동\s*\d+호.*$", "", text)
    text = re.sub(r"(\d+(?:-\d+)?)\s*번지.*$", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


class KakaoGeocoder:
    def __init__(self, key: str, cache_dir: Path | str, timeout: float = 10.0,
                 session: requests.Session | None = None) -> None:
        self.key = key.strip()
        self.timeout = timeout
        self._session = session
        self._local = threading.local()
        self._lock = threading.Lock()
        self.path = Path(cache_dir) / "geocode.json"
        try:
            self._cache: dict[str, list[float] | None] = json.loads(
                self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._cache = {}
        self.failed_key = ""

    @property
    def session(self):
        if self._session is not None:
            return self._session
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = self._local.session = requests.Session()
        return sess

    def coord(self, address: str) -> tuple[float, float] | None:
        """(위도, 경도). 못 찾으면 None. 키가 틀리면 KakaoKeyError (한 번 나면 더 묻지 않는다)."""
        query = clean_address(address)
        if not query:
            return None
        with self._lock:
            if query in self._cache:
                hit = self._cache[query]
                return tuple(hit) if hit else None
        if self.failed_key:
            raise KakaoKeyError(self.failed_key)
        found = None
        for q in (query, " ".join(query.split()[:4])):
            resp = self.session.get(URL, params={"query": q},
                                    headers={"Authorization": f"KakaoAK {self.key}"},
                                    timeout=self.timeout)
            if resp.status_code in (401, 403):
                self.failed_key = ("카카오 키가 맞지 않거나 '카카오맵' 사용이 꺼져 있습니다 "
                                   f"(HTTP {resp.status_code}). 읍·면·동 기준으로 잽니다.")
                raise KakaoKeyError(self.failed_key)
            resp.raise_for_status()
            docs = resp.json().get("documents") or []
            if docs:
                found = (round(float(docs[0]["y"]), 6), round(float(docs[0]["x"]), 6))
                break
        with self._lock:
            self._cache[query] = list(found) if found else None
        return found

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
