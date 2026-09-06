"""SQLite 기반 재고 관리 저장소.

파일 하나(기본: inventory.db)로 동작해서 별도 서버 설치가 필요 없다. 입고/출고
기록을 계속 쌓아두고, 품목별 현재 재고량은 그 기록들의 합으로 계산한다
(현재 재고를 직접 UPDATE하지 않으니 기록만 정확히 남기면 재고량은 항상
입출고 이력에서 다시 계산해서 맞출 수 있다).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = "inventory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    unit TEXT NOT NULL DEFAULT '개'
);

CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    change_qty INTEGER NOT NULL,
    memo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


@dataclass
class Item:
    id: int
    name: str
    unit: str


@dataclass
class Movement:
    id: int
    item_id: int
    item_name: str
    change_qty: int
    memo: str
    created_at: str


@dataclass
class StockLevel:
    item: Item
    quantity: int


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


def get_or_create_item(conn: sqlite3.Connection, name: str, unit: str = "개") -> Item:
    row = conn.execute("SELECT id, name, unit FROM items WHERE name = ?", (name,)).fetchone()
    if row is None:
        cursor = conn.execute("INSERT INTO items (name, unit) VALUES (?, ?)", (name, unit))
        return Item(id=cursor.lastrowid, name=name, unit=unit)
    return Item(id=row["id"], name=row["name"], unit=row["unit"])


def record_movement(
    db_path: str | Path,
    item_name: str,
    change_qty: int,
    memo: str = "",
    unit: str = "개",
) -> Movement:
    """재고 변동을 기록한다. change_qty: 입고면 양수, 출고면 음수로 넘긴다."""
    with connect(db_path) as conn:
        item = get_or_create_item(conn, item_name, unit)
        created_at = datetime.now().isoformat(timespec="seconds")
        cursor = conn.execute(
            "INSERT INTO movements (item_id, change_qty, memo, created_at) VALUES (?, ?, ?, ?)",
            (item.id, change_qty, memo, created_at),
        )
        return Movement(
            id=cursor.lastrowid,
            item_id=item.id,
            item_name=item.name,
            change_qty=change_qty,
            memo=memo,
            created_at=created_at,
        )


def current_stock(db_path: str | Path = DEFAULT_DB_PATH) -> list[StockLevel]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT items.id, items.name, items.unit, COALESCE(SUM(movements.change_qty), 0) AS quantity
            FROM items
            LEFT JOIN movements ON movements.item_id = items.id
            GROUP BY items.id
            ORDER BY items.name
            """
        ).fetchall()
        return [
            StockLevel(item=Item(id=r["id"], name=r["name"], unit=r["unit"]), quantity=r["quantity"])
            for r in rows
        ]


def recent_movements(db_path: str | Path = DEFAULT_DB_PATH, limit: int = 20) -> list[Movement]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT movements.id, movements.item_id, items.name AS item_name,
                   movements.change_qty, movements.memo, movements.created_at
            FROM movements
            JOIN items ON items.id = movements.item_id
            ORDER BY movements.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            Movement(
                id=r["id"],
                item_id=r["item_id"],
                item_name=r["item_name"],
                change_qty=r["change_qty"],
                memo=r["memo"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
