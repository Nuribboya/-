from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from resell_radar.models import Release, Score

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    collaborators TEXT NOT NULL DEFAULT '',
    release_at TEXT,
    first_seen TEXT NOT NULL,
    level TEXT,
    verdict TEXT
);
CREATE TABLE IF NOT EXISTS prices (
    key TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    retail_price INTEGER,
    market_price INTEGER
);
CREATE TABLE IF NOT EXISTS calendar_items (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    site TEXT NOT NULL,
    release_at TEXT NOT NULL,
    url TEXT NOT NULL,
    us_price INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_pairs (
    name TEXT PRIMARY KEY,
    usd INTEGER NOT NULL,
    krw INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (key, kind)
);
"""


class Store:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def recent_collab_counts(self, release: Release, collaborators: list[str]) -> dict[str, int]:
        """How many other releases with each partner were seen in the year before this one."""
        anchor = release.release_at or release.entry_start or datetime.now(timezone.utc)
        since = (anchor - timedelta(days=365)).isoformat()
        rows = self.conn.execute(
            "SELECT collaborators FROM releases WHERE key != ? AND COALESCE(release_at, first_seen) >= ?",
            (release.key, since),
        ).fetchall()
        counts = {}
        for name in collaborators:
            counts[name] = sum(1 for (c,) in rows if name in c.split("|"))
        return counts

    def save(self, release: Release, score: Score) -> None:
        now = datetime.now(timezone.utc).isoformat()
        release_at = release.release_at.isoformat() if release.release_at else None
        self.conn.execute(
            """INSERT INTO releases (key, name, collaborators, release_at, first_seen, level, verdict)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET name=excluded.name, collaborators=excluded.collaborators,
                   release_at=excluded.release_at, level=excluded.level, verdict=excluded.verdict""",
            (release.key, release.name, "|".join(score.collaborators), release_at, now, score.level, score.verdict),
        )
        # Price history is what later lets the grades be recalibrated from
        # real numbers instead of the hand-written tier table.
        self.conn.execute(
            "INSERT INTO prices (key, seen_at, retail_price, market_price) VALUES (?, ?, ?, ?)",
            (release.key, now, release.retail_price, release.market_price),
        )
        self.conn.commit()

    def market_observations(self):
        """Latest recorded market price per release, as learning observations."""
        from resell_radar.learning import Observation

        rows = self.conn.execute(
            """SELECT r.name, r.level, p.retail_price, p.market_price
               FROM prices p JOIN releases r ON r.key = p.key
               WHERE p.market_price > 0 AND p.retail_price > 0 AND r.level IS NOT NULL
               ORDER BY p.seen_at"""
        ).fetchall()
        latest = {name: Observation(name, level, retail, market) for name, level, retail, market in rows}
        return list(latest.values())

    def remember_calendar(self, items: list[Release]) -> list[Release]:
        """Keep US-calendar items until their release date.

        RSS feeds only carry the newest few dozen articles, so a drop announced
        weeks ahead would fall off the feed long before it releases.
        Returns every remembered item that hasn't released yet.
        """
        for r in items:
            self.conn.execute(
                """INSERT INTO calendar_items (source_id, name, site, release_at, url, us_price)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_id) DO UPDATE SET name=excluded.name, release_at=excluded.release_at,
                       us_price=COALESCE(excluded.us_price, calendar_items.us_price)""",
                (r.source_id, r.name, r.brand, r.release_at.isoformat(), r.url, r.us_price),
            )
        self.conn.commit()
        since = datetime.now(timezone.utc) - timedelta(days=1)
        out = []
        for source_id, name, site, release_at, url, usd in self.conn.execute(
            "SELECT source_id, name, site, release_at, url, us_price FROM calendar_items"
        ):
            when = datetime.fromisoformat(release_at)
            if when >= since:
                out.append(Release(source="us_calendar", source_id=source_id, name=name, brand=site,
                                   release_at=when, url=url, us_price=usd))
        return out

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def save_price_pair(self, name: str, usd: int, krw: int) -> None:
        self.conn.execute("INSERT OR REPLACE INTO price_pairs (name, usd, krw) VALUES (?, ?, ?)", (name, usd, krw))
        self.conn.commit()

    def price_pairs(self) -> list[tuple[str, int, int]]:
        return self.conn.execute("SELECT name, usd, krw FROM price_pairs").fetchall()

    def was_notified(self, key: str, kind: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM notifications WHERE key=? AND kind=?", (key, kind)).fetchone()
        return row is not None

    def mark_notified(self, key: str, kind: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO notifications (key, kind, sent_at) VALUES (?, ?, ?)",
            (key, kind, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
