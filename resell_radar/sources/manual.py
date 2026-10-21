from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from pathlib import Path

from resell_radar.models import DRAW, FCFS, KST, UNKNOWN, Release

# Hand-maintained schedule (any shop: 무신사, 크림 드로우, 편집숍, 해외 등).
# Also the place to type a real KREAM price into 예상시세 — when present it
# replaces the grade-based estimate.
COLUMNS = ["상품명", "발매처", "응모시작", "응모마감", "발매일", "정가", "방식", "발매처수", "재발매", "예상시세", "링크"]
EXAMPLE_ROW = {
    "상품명": "(예시) Fragment x Union x Air Jordan 1 High",
    "발매처": "무신사",
    "응모시작": "2026-11-01 10:00",
    "응모마감": "2026-11-02 23:59",
    "발매일": "2026-11-05",
    "정가": "219000",
    "방식": "응모",
    "발매처수": "2",
    "재발매": "N",
    "예상시세": "",
    "링크": "",
}
METHOD_WORDS = {"응모": DRAW, "드로우": DRAW, "draw": DRAW, "선착순": FCFS, "fcfs": FCFS}


def write_template(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(EXAMPLE_ROW)


def _int(value: str | None) -> int | None:
    value = (value or "").replace(",", "").replace("원", "").strip()
    return int(value) if value.isdigit() else None


def _dt(value: str | None) -> datetime | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def load(path: Path) -> list[Release]:
    if not path.exists():
        return []
    releases = []
    # utf-8-sig handles the BOM Excel adds when saving as "CSV UTF-8".
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("상품명") or "").strip()
            if not name or name.startswith("(예시)"):
                continue
            shop = (row.get("발매처") or "").strip()
            releases.append(
                Release(
                    source="manual",
                    source_id=hashlib.sha1(f"{name}|{shop}".encode()).hexdigest()[:12],
                    name=name,
                    brand=shop,
                    retail_price=_int(row.get("정가")),
                    release_at=_dt(row.get("발매일")),
                    entry_start=_dt(row.get("응모시작")),
                    entry_end=_dt(row.get("응모마감")),
                    method=METHOD_WORDS.get((row.get("방식") or "").strip().lower(), UNKNOWN),
                    retailer_count=_int(row.get("발매처수")),
                    is_restock=(row.get("재발매") or "").strip().upper() in ("Y", "O", "예", "YES"),
                    market_price=_int(row.get("예상시세")),
                    url=(row.get("링크") or "").strip(),
                )
            )
    return releases
