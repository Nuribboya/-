"""SQLite 저장소: 채널/영상 메타데이터와 수집 시점별 시계열 통계.

테이블
- channels        : 채널 메타데이터 (업로드 재생목록 ID 캐시 포함)
- channel_stats   : 채널 단위 시계열 (구독자, 총 조회수, 영상 수)
- videos          : 영상 메타데이터 (제목, 게시일, 카테고리, 길이, 태그)
- video_stats     : 영상 단위 시계열 (수집 시각별 조회수/좋아요/댓글)
- categories      : YouTube 카테고리 ID → 이름 캐시
- alerts          : 전송한 알림 이력 (쿨다운 판단용)

모든 시각은 UTC ISO-8601 문자열("2026-09-24T12:00:00+00:00")로 저장한다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id           TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    handle               TEXT,
    uploads_playlist_id  TEXT,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_stats (
    channel_id        TEXT NOT NULL REFERENCES channels(channel_id),
    collected_at      TEXT NOT NULL,
    subscriber_count  INTEGER,
    view_count        INTEGER,
    video_count       INTEGER,
    PRIMARY KEY (channel_id, collected_at)
);

CREATE TABLE IF NOT EXISTS videos (
    video_id          TEXT PRIMARY KEY,
    channel_id        TEXT NOT NULL REFERENCES channels(channel_id),
    title             TEXT NOT NULL,
    published_at      TEXT NOT NULL,
    category_id       TEXT,
    duration_seconds  INTEGER,
    tags              TEXT,            -- JSON 배열
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_channel_pub ON videos(channel_id, published_at);

CREATE TABLE IF NOT EXISTS video_stats (
    video_id       TEXT NOT NULL REFERENCES videos(video_id),
    collected_at   TEXT NOT NULL,
    view_count     INTEGER,
    like_count     INTEGER,            -- 비공개 설정 시 NULL
    comment_count  INTEGER,            -- 댓글 사용 중지 시 NULL
    PRIMARY KEY (video_id, collected_at)
);

CREATE TABLE IF NOT EXISTS categories (
    category_id  TEXT PRIMARY KEY,
    title        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    reason        TEXT NOT NULL,
    drop_pct      REAL,
    message       TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_channel ON alerts(channel_id, created_at);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(value: str) -> datetime:
    # YouTube API는 "2026-09-01T10:00:00Z" 형식을 준다.
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class Video:
    video_id: str
    channel_id: str
    title: str
    published_at: datetime
    category_id: str | None
    category_name: str | None
    duration_seconds: int | None
    tags: list[str]


@dataclass
class Snapshot:
    collected_at: datetime
    view_count: int | None
    like_count: int | None
    comment_count: int | None


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 쓰기 ---------------------------------------------------------------

    def upsert_channel(self, channel_id: str, title: str, handle: str | None,
                       uploads_playlist_id: str | None, now: datetime) -> None:
        self.conn.execute(
            """INSERT INTO channels (channel_id, title, handle, uploads_playlist_id, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 title=excluded.title,
                 handle=COALESCE(excluded.handle, channels.handle),
                 uploads_playlist_id=excluded.uploads_playlist_id,
                 updated_at=excluded.updated_at""",
            (channel_id, title, handle, uploads_playlist_id, to_iso(now)),
        )

    def add_channel_stats(self, channel_id: str, collected_at: datetime,
                          subscriber_count: int | None, view_count: int | None,
                          video_count: int | None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO channel_stats
               (channel_id, collected_at, subscriber_count, view_count, video_count)
               VALUES (?, ?, ?, ?, ?)""",
            (channel_id, to_iso(collected_at), subscriber_count, view_count, video_count),
        )

    def upsert_video(self, video_id: str, channel_id: str, title: str,
                     published_at: datetime, category_id: str | None,
                     duration_seconds: int | None, tags: list[str], now: datetime) -> None:
        self.conn.execute(
            """INSERT INTO videos (video_id, channel_id, title, published_at, category_id,
                                   duration_seconds, tags, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                 title=excluded.title,
                 category_id=excluded.category_id,
                 duration_seconds=excluded.duration_seconds,
                 tags=excluded.tags,
                 updated_at=excluded.updated_at""",
            (video_id, channel_id, title, to_iso(published_at), category_id,
             duration_seconds, json.dumps(tags, ensure_ascii=False), to_iso(now)),
        )

    def add_video_stats(self, video_id: str, collected_at: datetime, view_count: int | None,
                        like_count: int | None, comment_count: int | None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO video_stats
               (video_id, collected_at, view_count, like_count, comment_count)
               VALUES (?, ?, ?, ?, ?)""",
            (video_id, to_iso(collected_at), view_count, like_count, comment_count),
        )

    def upsert_categories(self, categories: dict[str, str]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO categories (category_id, title) VALUES (?, ?)",
            list(categories.items()),
        )

    def add_alert(self, channel_id: str, created_at: datetime, reason: str,
                  drop_pct: float | None, message: str) -> None:
        self.conn.execute(
            "INSERT INTO alerts (channel_id, created_at, reason, drop_pct, message) VALUES (?, ?, ?, ?, ?)",
            (channel_id, to_iso(created_at), reason, drop_pct, message),
        )

    def commit(self) -> None:
        self.conn.commit()

    # ---- 읽기 ---------------------------------------------------------------

    def get_channel(self, channel_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()

    def find_channel_by_handle(self, handle: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM channels WHERE lower(handle) = lower(?)", (handle,)
        ).fetchone()

    def known_category_ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT category_id FROM categories")}

    def get_videos(self, channel_id: str, limit: int | None = None) -> list[Video]:
        """최신 게시일 순으로 영상 목록을 반환한다."""
        sql = """SELECT v.*, c.title AS category_name FROM videos v
                 LEFT JOIN categories c ON c.category_id = v.category_id
                 WHERE v.channel_id = ? ORDER BY v.published_at DESC"""
        params: tuple = (channel_id,)
        if limit:
            sql += " LIMIT ?"
            params += (limit,)
        return [
            Video(
                video_id=r["video_id"],
                channel_id=r["channel_id"],
                title=r["title"],
                published_at=from_iso(r["published_at"]),
                category_id=r["category_id"],
                category_name=r["category_name"],
                duration_seconds=r["duration_seconds"],
                tags=json.loads(r["tags"] or "[]"),
            )
            for r in self.conn.execute(sql, params)
        ]

    def get_snapshots(self, video_id: str) -> list[Snapshot]:
        """수집 시각 오름차순 시계열."""
        return [
            Snapshot(from_iso(r["collected_at"]), r["view_count"], r["like_count"], r["comment_count"])
            for r in self.conn.execute(
                "SELECT * FROM video_stats WHERE video_id = ? ORDER BY collected_at", (video_id,)
            )
        ]

    def get_channel_stats(self, channel_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM channel_stats WHERE channel_id = ? ORDER BY collected_at", (channel_id,)
        ).fetchall()

    def last_alert(self, channel_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM alerts WHERE channel_id = ? ORDER BY created_at DESC LIMIT 1",
            (channel_id,),
        ).fetchone()
