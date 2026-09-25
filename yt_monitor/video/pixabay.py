"""Pixabay 무료 스톡 영상 (Pexels와 함께 써서 영상 선택 폭을 넓힌다).

API 키 발급 (무료, 즉시): https://pixabay.com/api/docs/ → 가입/로그인하면 문서 페이지 "key" 항목에 키가 보인다.
요청 한도: 60초에 100회. 상업적 이용 가능, 출처 표기 권장.

단독 테스트:
    python -m yt_monitor.video.pixabay "ocean waves" --key 발급받은키 --out samples/pixabay
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import requests

from .pexels import Clip, PexelsAuthError, PexelsError, download_clip, pick_file

log = logging.getLogger(__name__)

API_URL = "https://pixabay.com/api/videos/"
SIGNUP_URL = "https://pixabay.com/api/docs/"

KEY_HELP = f"""Pixabay API 키 (선택, 무료)

1. {SIGNUP_URL} 에 접속해 가입/로그인합니다.
2. 같은 페이지의 "Parameters" 표에서 key 항목에 본인 API 키가 표시됩니다.
3. 프로그램의 [⚙ 설정] 창 'Pixabay API 키' 칸에 붙여넣고 저장하세요.
   키를 넣으면 Pexels와 함께 검색해서 더 다양한 영상을 고릅니다."""


class PixabayClient:
    source = "pixabay"

    def __init__(self, api_key: str, *, per_page: int = 15, timeout: float = 60,
                 target_size: tuple[int, int] = (1080, 1920), api_url: str = API_URL,
                 session: requests.Session | None = None):
        if not api_key:
            raise PexelsError(KEY_HELP)
        self.api_key = api_key
        self.per_page = max(3, min(int(per_page), 200))
        self.timeout = timeout
        self.target_size = target_size
        self.api_url = api_url
        self.session = session or requests.Session()
        self._cache: dict[str, list[Clip]] = {}
        self.requests_made = 0

    @classmethod
    def from_config(cls, cfg) -> "PixabayClient":
        v = cfg.video
        return cls(cfg.secret("pixabay", "api_key", required=False) or "",
                   per_page=int(cfg.pixabay.get("per_page", 15)),
                   timeout=float(cfg.pexels.get("timeout_sec", 60)),
                   target_size=(int(v["width"]), int(v["height"])))

    def search(self, query: str) -> list[Clip]:
        key = query.lower().strip()
        if key in self._cache:
            return self._cache[key]
        params = {"key": self.api_key, "q": query[:100], "per_page": self.per_page, "safesearch": "true"}
        for attempt in range(3):
            try:
                resp = self.session.get(self.api_url, params=params, timeout=self.timeout)
                self.requests_made += 1
            except requests.RequestException as exc:
                if attempt == 2:
                    raise PexelsError(f"Pixabay 연결 실패: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (400, 401, 403) and "key" in resp.text.lower():
                raise PexelsAuthError("Pixabay API 키가 올바르지 않습니다. 설정에서 키를 확인하세요.\n\n" + KEY_HELP)
            if resp.status_code == 429:
                raise PexelsAuthError("Pixabay 요청 한도(60초에 100회)를 넘었습니다. 잠시 뒤 다시 시도하세요.")
            if resp.status_code >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise PexelsError(f"Pixabay 오류 {resp.status_code}: {resp.text[:200]}")
            break
        w, h = self.target_size
        clips = []
        for hit in resp.json().get("hits", []):
            files = [{"id": i, "link": f.get("url"), "width": f.get("width"), "height": f.get("height")}
                     for i, f in enumerate((hit.get("videos") or {}).values()) if f and f.get("url")]
            f = pick_file(files, w, h)
            if f is None:
                continue
            clips.append(Clip(video_id=int(hit["id"]), file_id=int(f["id"]), url=f["link"],
                              width=int(f["width"]), height=int(f["height"]),
                              duration=float(hit.get("duration") or 0), page_url=hit.get("pageURL", ""),
                              author=hit.get("user", ""), query=query, source=self.source))
        # 세로 영상을 앞으로 (Pixabay 영상 검색은 방향 필터가 없다)
        portrait = h >= w
        clips.sort(key=lambda c: (c.height >= c.width) != portrait)
        log.info("Pixabay 검색 '%s' → %d개", query, len(clips))
        self._cache[key] = clips
        return clips

    def download(self, clip: Clip, cache_dir: Path, cancel: threading.Event | None = None) -> Path:
        return download_clip(self.session, clip, cache_dir, self.timeout, cancel)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Pixabay 영상 검색/다운로드 단독 테스트")
    parser.add_argument("keywords", nargs="+")
    parser.add_argument("--key", default=os.environ.get("PIXABAY_API_KEY", ""))
    parser.add_argument("--out", default="samples/pixabay")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.key:
        print(KEY_HELP)
        return 1
    client = PixabayClient(args.key)
    for kw in args.keywords:
        clips = client.search(kw)
        print(f"'{kw}': {len(clips)}개")
        if clips:
            print(f"  ✔ {client.download(clips[0], Path(args.out))}  {clips[0].credit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
