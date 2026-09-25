"""Pexels 무료 스톡 영상 검색 · 다운로드.

API 키 발급 (무료, 즉시): https://www.pexels.com/api/ → 가입/로그인 → "Your API Key"
요청 한도: 시간당 200회, 월 20,000회 (영상 1편에 보통 10~30회 사용)
Pexels 이용 조건상 출처 표기는 선택이지만 권장된다 → 영상과 함께 <이름>_출처.txt 를 저장한다.

단독 테스트 (키워드 → 클립 다운로드):
    python -m yt_monitor.video.pexels "ocean waves" --out samples/pexels
    python -m yt_monitor.video.pexels "coffee" "city night" --count 2 --key 발급받은키
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.pexels.com/videos/search"
SIGNUP_URL = "https://www.pexels.com/api/"

KEY_HELP = f"""Pexels API 키가 필요합니다 (무료, 가입 즉시 발급).

1. {SIGNUP_URL} 에 접속해 가입/로그인합니다.
2. "Your API Key" 페이지에서 사용 목적(예: 개인 유튜브 영상 제작)을 간단히 적으면 키가 바로 나옵니다.
3. 프로그램의 [⚙ 설정] 창 'Pexels API 키' 칸에 붙여넣고 저장하세요.
   (또는 config.yaml 의 pexels.api_key / .env 의 PEXELS_API_KEY)"""


class PexelsError(RuntimeError):
    pass


class PexelsAuthError(PexelsError):
    """키가 틀렸거나 한도 초과 → 계속 진행해도 소용없는 오류."""


@dataclass
class Clip:
    video_id: int
    file_id: int
    url: str               # 다운로드 주소
    width: int
    height: int
    duration: float
    page_url: str          # 출처(영상 페이지)
    author: str
    query: str
    path: Path | None = None
    source: str = "pexels"          # pexels | pixabay

    @property
    def key(self) -> tuple[str, int]:
        return (self.source, self.video_id)

    @property
    def credit(self) -> str:
        return f"{self.author} / {self.source.capitalize()} — {self.page_url}"


def pick_file(video_files: list[dict], target_w: int, target_h: int) -> dict | None:
    """video_files 중 출력 해상도에 가장 알맞은 파일 (너무 크면 다운로드만 오래 걸림).

    목표 방향(세로/가로)이 같은 mp4 중 짧은 변이 목표의 2/3 이상인 것 가운데 가장 작은 것,
    없으면 가장 큰 것.
    """
    portrait = target_h >= target_w
    files = [f for f in video_files if f.get("link") and f.get("width") and f.get("height")
             and (f.get("file_type") or "video/mp4") == "video/mp4"]
    if not files:
        return None
    same = [f for f in files if (f["height"] >= f["width"]) == portrait] or files
    need = min(target_w, target_h) * 2 / 3
    good = [f for f in same if min(f["width"], f["height"]) >= need]
    if good:
        return min(good, key=lambda f: f["width"] * f["height"])
    return max(same, key=lambda f: f["width"] * f["height"])


class PexelsClient:
    def __init__(self, api_key: str, *, orientation: str = "portrait", per_page: int = 15,
                 timeout: float = 60, target_size: tuple[int, int] = (1080, 1920),
                 api_url: str = API_URL, session: requests.Session | None = None):
        if not api_key:
            raise PexelsError(KEY_HELP)
        self.api_key = api_key
        self.orientation = orientation
        self.per_page = per_page
        self.timeout = timeout
        self.target_size = target_size
        self.api_url = api_url
        self.session = session or requests.Session()
        self._cache: dict[tuple[str, str], list[Clip]] = {}
        self.requests_made = 0

    @classmethod
    def from_config(cls, cfg) -> "PexelsClient":
        p, v = cfg.pexels, cfg.video
        return cls(cfg.secret("pexels", "api_key", required=False) or "",
                   orientation=p.get("orientation", "portrait"), per_page=int(p.get("per_page", 15)),
                   timeout=float(p.get("timeout_sec", 60)),
                   target_size=(int(v["width"]), int(v["height"])))

    def search(self, query: str, locale: str | None = None) -> list[Clip]:
        """키워드로 영상 검색 → 다운로드 가능한 Clip 목록 (같은 검색어는 캐시)."""
        key = (query.lower().strip(), locale or "")
        if key in self._cache:
            return self._cache[key]
        params = {"query": query, "per_page": self.per_page, "size": "medium"}
        if self.orientation:
            params["orientation"] = self.orientation
        if locale:
            params["locale"] = locale
        for attempt in range(3):
            try:
                resp = self.session.get(self.api_url, params=params, timeout=self.timeout,
                                        headers={"Authorization": self.api_key})
                self.requests_made += 1
            except requests.RequestException as exc:
                if attempt == 2:
                    raise PexelsError(f"Pexels 연결 실패: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 401 or resp.status_code == 403:
                raise PexelsAuthError("Pexels API 키가 올바르지 않습니다 (401/403). 설정에서 키를 확인하세요.\n\n"
                                  + KEY_HELP)
            if resp.status_code == 429:
                raise PexelsAuthError("Pexels 요청 한도(시간당 200회)를 넘었습니다. 잠시 뒤 다시 시도하세요.")
            if resp.status_code >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise PexelsError(f"Pexels 오류 {resp.status_code}: {resp.text[:200]}")
            break
        clips = []
        w, h = self.target_size
        for v in resp.json().get("videos", []):
            f = pick_file(v.get("video_files") or [], w, h)
            if f is None:
                continue
            clips.append(Clip(video_id=int(v["id"]), file_id=int(f.get("id") or 0), url=f["link"],
                              width=int(f["width"]), height=int(f["height"]),
                              duration=float(v.get("duration") or 0), page_url=v.get("url", ""),
                              author=(v.get("user") or {}).get("name", ""), query=query))
        log.info("Pexels 검색 '%s' → %d개", query, len(clips))
        self._cache[key] = clips
        return clips

    def download(self, clip: Clip, cache_dir: Path, cancel: threading.Event | None = None) -> Path:
        return download_clip(self.session, clip, cache_dir, self.timeout, cancel)


def download_clip(session: requests.Session, clip: Clip, cache_dir: Path, timeout: float = 60,
                  cancel: threading.Event | None = None) -> Path:
    """클립을 cache_dir에 받는다. 이미 받은 파일이면 다시 받지 않는다 (Pexels·Pixabay 공용)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{clip.source}_{clip.video_id}_{clip.file_id}.mp4"
    if path.exists() and path.stat().st_size > 0:
        clip.path = path
        return path
    tmp = path.with_suffix(".part")
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with session.get(clip.url, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(1 << 16):
                        if cancel is not None and cancel.is_set():
                            raise InterruptedError
                        f.write(chunk)
            os.replace(tmp, path)
            clip.path = path
            log.info("다운로드 완료: %s (%.1f MB)", path.name, path.stat().st_size / 1e6)
            return path
        except InterruptedError:
            tmp.unlink(missing_ok=True)
            raise
        except (requests.RequestException, OSError) as exc:
            last_exc = exc
            tmp.unlink(missing_ok=True)
            time.sleep(2 ** attempt)
    raise PexelsError(f"영상 다운로드 실패 ({clip.url}): {last_exc}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Pexels 영상 검색/다운로드 단독 테스트")
    parser.add_argument("keywords", nargs="+", help="검색 키워드 (영어가 결과가 많음)")
    parser.add_argument("--out", default="samples/pexels", help="저장 폴더")
    parser.add_argument("--count", type=int, default=1, help="키워드당 받을 영상 수")
    parser.add_argument("--key", help="API 키 (생략하면 config.yaml / PEXELS_API_KEY)")
    parser.add_argument("--config", help="config.yaml 경로")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    key = args.key or os.environ.get("PEXELS_API_KEY", "")
    if not key:
        from ..config import Config, read_raw
        from ..paths import default_config_path

        path = Path(args.config) if args.config else default_config_path()
        try:
            from dotenv import load_dotenv

            load_dotenv(path.parent / ".env", override=False)
        except ImportError:
            pass
        key = Config(read_raw(path), path.parent).secret("pexels", "api_key", required=False) or ""
    if not key:
        print(KEY_HELP)
        return 1
    client = PexelsClient(key)
    for kw in args.keywords:
        clips = client.search(kw)
        print(f"\n'{kw}': {len(clips)}개 검색됨")
        for c in clips[: args.count]:
            path = client.download(c, Path(args.out))
            print(f"  ✔ {path}  ({c.width}x{c.height}, {c.duration:.0f}초)  {c.credit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
