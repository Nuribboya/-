"""채점 결과를 SQLite에 저장해서, 실행할 때마다 사라지지 않고 웹 UI에서
계속 조회/관리할 수 있게 한다.

여기에 실제 컨택 결과(성사/거절 등)를 기록해두면, 나중에 "우리가 실제로
성사시킨 회사들의 공통 패턴"을 학습하는 진짜 모델을 만들 때 그 데이터가 된다.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .models import ScoredCandidate

DEFAULT_DB_PATH = "leadradar_results.db"

CONTACT_STATUSES = ["미접촉", "컨택함", "협의중", "성사", "거절", "보류"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    name TEXT PRIMARY KEY,
    business_description TEXT NOT NULL,
    distance_km REAL,
    revenue_growth_pct REAL,
    fit_score INTEGER NOT NULL,
    conflicts_with_excluded_client INTEGER NOT NULL,
    reasoning TEXT NOT NULL,
    source TEXT NOT NULL,
    contact_status TEXT NOT NULL DEFAULT '미접촉',
    updated_at TEXT NOT NULL
);
"""


@dataclass
class StoredCandidate:
    name: str
    business_description: str
    distance_km: Optional[float]
    revenue_growth_pct: Optional[float]
    fit_score: int
    conflicts_with_excluded_client: bool
    reasoning: str
    source: str
    contact_status: str
    updated_at: str


@contextmanager
def connect(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def upsert_scored_candidates(db_path: str | Path, results: list[ScoredCandidate]) -> None:
    """이름이 같으면 최신 채점 결과로 덮어쓰되, 기존에 기록해둔 contact_status는 유지한다."""
    now = datetime.now().isoformat(timespec="seconds")
    with connect(db_path) as conn:
        for r in results:
            existing = conn.execute(
                "SELECT contact_status FROM candidates WHERE name = ?", (r.candidate.name,)
            ).fetchone()
            contact_status = existing["contact_status"] if existing else "미접촉"
            conn.execute(
                """
                INSERT INTO candidates (
                    name, business_description, distance_km, revenue_growth_pct,
                    fit_score, conflicts_with_excluded_client, reasoning, source,
                    contact_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    business_description = excluded.business_description,
                    distance_km = excluded.distance_km,
                    revenue_growth_pct = excluded.revenue_growth_pct,
                    fit_score = excluded.fit_score,
                    conflicts_with_excluded_client = excluded.conflicts_with_excluded_client,
                    reasoning = excluded.reasoning,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    r.candidate.name,
                    r.candidate.business_description,
                    r.candidate.distance_km,
                    r.candidate.revenue_growth_pct,
                    r.fit_score,
                    int(r.conflicts_with_excluded_client),
                    r.reasoning,
                    r.candidate.source,
                    contact_status,
                    now,
                ),
            )


def list_candidates(db_path: str | Path = DEFAULT_DB_PATH) -> list[StoredCandidate]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM candidates ORDER BY fit_score DESC").fetchall()
        return [
            StoredCandidate(
                name=r["name"],
                business_description=r["business_description"],
                distance_km=r["distance_km"],
                revenue_growth_pct=r["revenue_growth_pct"],
                fit_score=r["fit_score"],
                conflicts_with_excluded_client=bool(r["conflicts_with_excluded_client"]),
                reasoning=r["reasoning"],
                source=r["source"],
                contact_status=r["contact_status"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]


def update_contact_status(db_path: str | Path, name: str, status: str) -> None:
    if status not in CONTACT_STATUSES:
        raise ValueError(f"알 수 없는 상태입니다: {status}")
    with connect(db_path) as conn:
        conn.execute("UPDATE candidates SET contact_status = ? WHERE name = ?", (status, name))
