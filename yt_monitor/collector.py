"""YouTube Data API v3 수집기.

쿼터 사용량 (기본 무료 쿼터: 하루 10,000 unit)
- channels.list         : 1 unit / 채널
- playlistItems.list    : 1 unit / 50개 영상
- videos.list           : 1 unit / 50개 영상
- videoCategories.list  : 1 unit (새 카테고리가 보일 때만)
→ 채널당 1회 수집에 약 3 unit. 6시간 주기(하루 4회)로 채널 10개를 돌려도 하루 ~120 unit.
search.list(100 unit)는 쓰지 않는다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from .config import ChannelConfig
from .db import Database, from_iso, utcnow

log = logging.getLogger(__name__)

_DURATION_RE = re.compile(
    r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def parse_duration(value: str | None) -> int | None:
    """ISO-8601 기간("PT1H2M3S")을 초로 변환."""
    if not value:
        return None
    m = _DURATION_RE.match(value)
    if not m:
        return None
    d, h, mi, s = (int(m.group(k) or 0) for k in ("d", "h", "m", "s"))
    return d * 86400 + h * 3600 + mi * 60 + s


def _int(value) -> int | None:
    return int(value) if value is not None else None


def build_youtube_service(api_key: str):
    from googleapiclient.discovery import build

    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


@dataclass
class CollectResult:
    channel_id: str
    channel_title: str
    video_count: int
    collected_at: datetime


class YouTubeCollector:
    def __init__(self, service, db: Database, max_videos: int = 30):
        self.yt = service
        self.db = db
        self.max_videos = max_videos

    # ---- 채널 -----------------------------------------------------------------

    def _fetch_channel(self, ch: ChannelConfig) -> dict:
        params = {"part": "snippet,contentDetails,statistics"}
        if ch.id:
            params["id"] = ch.id
        else:
            params["forHandle"] = ch.handle
        items = self.yt.channels().list(**params).execute().get("items", [])
        if not items:
            raise LookupError(f"채널을 찾을 수 없습니다: {ch.id or ch.handle}")
        return items[0]

    # ---- 영상 -----------------------------------------------------------------

    def _recent_video_ids(self, uploads_playlist_id: str) -> list[str]:
        ids: list[str] = []
        page_token = None
        while len(ids) < self.max_videos:
            resp = self.yt.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=min(50, self.max_videos - len(ids)),
                pageToken=page_token,
            ).execute()
            ids += [it["contentDetails"]["videoId"] for it in resp.get("items", [])]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids[: self.max_videos]

    def _fetch_videos(self, video_ids: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(video_ids), 50):
            resp = self.yt.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(video_ids[i : i + 50]),
                maxResults=50,
            ).execute()
            out += resp.get("items", [])
        return out

    def _ensure_categories(self, category_ids: set[str]) -> None:
        missing = category_ids - self.db.known_category_ids()
        if not missing:
            return
        resp = self.yt.videoCategories().list(part="snippet", id=",".join(sorted(missing))).execute()
        self.db.upsert_categories(
            {it["id"]: it["snippet"]["title"] for it in resp.get("items", [])}
        )

    # ---- 전체 ---------------------------------------------------------------

    def collect_channel(self, ch: ChannelConfig, now: datetime | None = None) -> CollectResult:
        now = now or utcnow()
        info = self._fetch_channel(ch)
        channel_id = info["id"]
        title = ch.name or info["snippet"]["title"]
        uploads = info["contentDetails"]["relatedPlaylists"]["uploads"]
        stats = info.get("statistics", {})
        handle = ch.handle or info["snippet"].get("customUrl")

        self.db.upsert_channel(channel_id, title, handle, uploads, now)
        self.db.add_channel_stats(
            channel_id, now,
            None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount")),
            _int(stats.get("viewCount")),
            _int(stats.get("videoCount")),
        )

        videos = self._fetch_videos(self._recent_video_ids(uploads))
        # 예정된 프리미어/라이브는 아직 조회수가 의미 없으므로 제외
        videos = [
            v for v in videos
            if v["snippet"].get("liveBroadcastContent", "none") != "upcoming"
            and from_iso(v["snippet"]["publishedAt"]) <= now
        ]
        self._ensure_categories({v["snippet"]["categoryId"] for v in videos if v["snippet"].get("categoryId")})

        for v in videos:
            sn, st = v["snippet"], v.get("statistics", {})
            self.db.upsert_video(
                v["id"], channel_id, sn["title"], from_iso(sn["publishedAt"]),
                sn.get("categoryId"),
                parse_duration(v.get("contentDetails", {}).get("duration")),
                sn.get("tags", []), now,
            )
            self.db.add_video_stats(
                v["id"], now, _int(st.get("viewCount")),
                _int(st.get("likeCount")), _int(st.get("commentCount")),
            )
        self.db.commit()
        log.info("수집 완료: %s (%s) 영상 %d개", title, channel_id, len(videos))
        return CollectResult(channel_id, title, len(videos), now)
