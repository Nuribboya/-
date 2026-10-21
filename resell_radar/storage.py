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

    def was_notified(self, key: str, kind: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM notifications WHERE key=? AND kind=?", (key, kind)).fetchone()
        return row is not None

    def mark_notified(self, key: str, kind: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO notifications (key, kind, sent_at) VALUES (?, ?, ?)",
            (key, kind, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
