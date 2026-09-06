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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_DB_PATH = "inventory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    unit TEXT NOT NULL DEFAULT '개',
    lead_time_days INTEGER NOT NULL DEFAULT 7
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
    lead_time_days: int = 7


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


@dataclass
class StockOutlook:
    item: Item
    quantity: int
    avg_daily_consumption: float
    days_until_depletion: Optional[float]
    needs_reorder: bool


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
        # 이 컬럼 추가 전에 만들어진 기존 DB 파일에도 안전하게 컬럼을 붙여준다.
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
        if "lead_time_days" not in existing_columns:
            conn.execute("ALTER TABLE items ADD COLUMN lead_time_days INTEGER NOT NULL DEFAULT 7")


def get_or_create_item(conn: sqlite3.Connection, name: str, unit: str = "개", lead_time_days: int = 7) -> Item:
    row = conn.execute("SELECT id, name, unit, lead_time_days FROM items WHERE name = ?", (name,)).fetchone()
    if row is None:
        cursor = conn.execute(
            "INSERT INTO items (name, unit, lead_time_days) VALUES (?, ?, ?)",
            (name, unit, lead_time_days),
        )
        return Item(id=cursor.lastrowid, name=name, unit=unit, lead_time_days=lead_time_days)
    return Item(id=row["id"], name=row["name"], unit=row["unit"], lead_time_days=row["lead_time_days"])


def set_lead_time_days(db_path: str | Path, item_name: str, lead_time_days: int) -> None:
    """이미 있는 품목의 리드타임(발주 후 실제 입고까지 걸리는 일수)을 갱신한다."""
    with connect(db_path) as conn:
        conn.execute("UPDATE items SET lead_time_days = ? WHERE name = ?", (lead_time_days, item_name))


def record_movement(
    db_path: str | Path,
    item_name: str,
    change_qty: int,
    memo: str = "",
    unit: str = "개",
    lead_time_days: int = 7,
) -> Movement:
    """재고 변동을 기록한다. change_qty: 입고면 양수, 출고면 음수로 넘긴다.

    lead_time_days는 새 품목을 처음 만들 때만 쓰인다(이미 있는 품목이면 무시됨) -
    나중에 바꾸려면 set_lead_time_days를 쓴다.
    """
    with connect(db_path) as conn:
        item = get_or_create_item(conn, item_name, unit, lead_time_days)
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
            SELECT items.id, items.name, items.unit, items.lead_time_days,
                   COALESCE(SUM(movements.change_qty), 0) AS quantity
            FROM items
            LEFT JOIN movements ON movements.item_id = items.id
            GROUP BY items.id
            ORDER BY items.name
            """
        ).fetchall()
        return [
            StockLevel(
                item=Item(id=r["id"], name=r["name"], unit=r["unit"], lead_time_days=r["lead_time_days"]),
                quantity=r["quantity"],
            )
            for r in rows
        ]


def stock_outlook(db_path: str | Path = DEFAULT_DB_PATH, lookback_days: int = 30) -> list[StockOutlook]:
    """최근 lookback_days 동안의 평균 일일 출고량으로 소진 예상일을 추정한다.

    딥러닝/통계 모델을 학습시키는 게 아니라 단순 평균 기반 추정이다. 데이터가
    몇 달치 쌓이면 이 기록들을 학습 데이터로 삼아 계절성/추세를 반영하는
    진짜 수요예측 모델로 발전시킬 수 있다.

    needs_reorder는 "예상 소진일이 리드타임(발주 후 실제 입고까지 걸리는 일수)
    이내"일 때 True가 된다 - 지금 발주 안 하면 다 떨어지기 전에 못 채운다는 뜻.
    부족(품절)도 과잉(재고 낭비)도 피하려면 이 시점에 딱 맞춰 발주하면 된다.
    """
    cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat(timespec="seconds")
    with connect(db_path) as conn:
        stock_rows = conn.execute(
            """
            SELECT items.id, items.name, items.unit, items.lead_time_days,
                   COALESCE(SUM(movements.change_qty), 0) AS quantity
            FROM items
            LEFT JOIN movements ON movements.item_id = items.id
            GROUP BY items.id
            ORDER BY items.name
            """
        ).fetchall()

        recent_out_rows = conn.execute(
            """
            SELECT item_id, -SUM(change_qty) AS total_out
            FROM movements
            WHERE change_qty < 0 AND created_at >= ?
            GROUP BY item_id
            """,
            (cutoff,),
        ).fetchall()

    recent_out_by_item = {r["item_id"]: r["total_out"] for r in recent_out_rows}

    outlook = []
    for r in stock_rows:
        total_out = recent_out_by_item.get(r["id"], 0)
        avg_daily = total_out / lookback_days if total_out else 0.0
        days_until = (r["quantity"] / avg_daily) if avg_daily > 0 else None
        needs_reorder = days_until is not None and days_until <= r["lead_time_days"]
        outlook.append(
            StockOutlook(
                item=Item(id=r["id"], name=r["name"], unit=r["unit"], lead_time_days=r["lead_time_days"]),
                quantity=r["quantity"],
                avg_daily_consumption=round(avg_daily, 2),
                days_until_depletion=round(days_until, 1) if days_until is not None else None,
                needs_reorder=needs_reorder,
            )
        )
    return outlook


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
