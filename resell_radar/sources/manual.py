from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from pathlib import Path

from resell_radar.models import DRAW, FCFS, KST, UNKNOWN, Release

# Hand-maintained schedule (any shop: 무신사, 크림 드로우, 편집숍, 해외 등).
# Also the place to type a real KREAM price into 예상시세 — when present it
# replaces the grade-based estimate.
COLUMNS = ["상품명", "발매처", "응모시작", "응모마감", "발매일", "정가", "방식", "발매처수", "재발매", "예상시세", "링크", "메모"]
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
    "메모": "예시 줄은 무시됩니다",
}

# Collabs found in public release calendars (Sneaker News, Sneakerfiles, Foot
# Locker etc.) as of 2026-10-21, so a first run shows something real. KR
# prices are US retail x 1,150 where only the US price was known; dates and
# prices should be checked on the shop's own page. Past dates drop out of the
# results automatically.
EST = "정가는 미국 정가 x1,150 추정"
STARTER_ROWS = [
    ("JJJJound x New Balance 990v3", "JJJJound 선발매", "2026-10-22", "", "", "https://sneakernews.com/2026/10/18/sneaker-releases-october-18-24-2026/", "다음 주 확대 발매 예정"),
    ("SZA x Vans", "Vans", "2026-10-22", "", "", "https://sneakernews.com/2026/10/18/sneaker-releases-october-18-24-2026/", ""),
    ("APB x Air Jordan 11 Low", "Jordan", "2026-10-23", "", "", "https://sneakernews.com/2026/10/18/sneaker-releases-october-18-24-2026/", ""),
    ("Nike Dunk Low Shibuya Halloween", "Nike", "2026-10-23", "149500", "", "https://sneakernews.com/2026/10/18/sneaker-releases-october-18-24-2026/", EST),
    ("Nike Kobe 3 Low Protro Mamba Eterno", "Nike", "2026-10-30", "218500", "", "https://sneakernews.com/category/release-dates/", EST),
    ("Air Jordan 4 Light Army", "Jordan", "2026-10-31", "", "", "https://sneakernews.com/air-jordan-release-dates/", ""),
    ("Space Jam x Air Jordan 1 Mid", "Jordan", "2026-11-05", "", "", "https://www.sneakerfiles.com/release-dates/", ""),
    ("Space Jam x Air Jordan 3 Lola Bunny", "Jordan", "2026-11-05", "", "", "https://www.sneakerfiles.com/release-dates/", ""),
    ("Victor Wembanyama x Nike GT Cut 4 Statue of Liberty", "Nike", "2026-11-05", "241500", "", "https://www.sneakerfiles.com/release-dates/", EST),
    ("Chainsaw Man x Crocs Classic Clog", "Crocs", "2026-11-11", "", "", "https://www.footlocker.com/release-calendar.html", ""),
    ("Pokemon x Puma", "Puma", "2026-11-12", "", "", "https://sneakernews.com/2026/08/03/adidas-pokemon-30th-anniversary-collection/", ""),
    ("Air Jordan 4 OG Bred", "Jordan", "2026-11-27", "", "Y", "https://sneakerreleasedates.net/calendar", ""),
    ("Slam Jam x Nike Air Max 97", "Nike", "2026-11-28", "", "", "https://www.sneakerfiles.com/release-dates/", ""),
    ("Pharrell x adidas Adistar Jellyfish Auburn Timber", "adidas", "2026-12-05", "345000", "", "https://www.sneakerfiles.com/release-dates/", EST),
    ("Marvel Avengers Doomsday x adidas Superstar Pack (12월 미정)", "adidas", "", "", "", "https://sneakernews.com/2026/09/30/marvel-adidas-superstar-avengers-doomsday/", "날짜 나오면 발매일 채우기"),
    ("Travis Scott x Nike Air Force 1 Low Ice Blue (날짜 미정)", "Nike", "", "", "", "https://www.sneakerfreaker.com/releases/travis-scott-nike-air-force-1-zoom-vapor-12-2026-price-buy-release-date", "날짜 나오면 발매일 채우기"),
    ("Satoshi Nakamoto x Vans Era 95 (날짜 미정)", "Vans", "", "", "", "https://www.soleretriever.com/sneaker-release-dates/vans", "날짜 나오면 발매일 채우기"),
    ("Sean Wotherspoon x Mizuno Wave MG4 (날짜 미정)", "Mizuno", "", "", "", "https://sneakernews.com/2026/10/20/sean-wotherspoon-mizuno-wave-mg4-ls", "날짜 나오면 발매일 채우기"),
    ("Supreme x Dyson FW26 (주간 드롭)", "Supreme", "", "", "", "https://www.highsnobiety.com/p/supreme-fw26/", "품목·정가 확인 필요"),
]
METHOD_WORDS = {"응모": DRAW, "드로우": DRAW, "draw": DRAW, "선착순": FCFS, "fcfs": FCFS}


def write_template(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(EXAMPLE_ROW)
        for name, shop, date, price, restock, link, memo in STARTER_ROWS:
            writer.writerow({
                "상품명": name, "발매처": shop, "발매일": date, "정가": price,
                "재발매": restock or "N", "링크": link, "메모": memo,
            })


def ensure_template(path: Path) -> None:
    """Create releases.csv, or refill one an older version left with only the example row."""
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if (r.get("상품명") or "").strip()]
        if any(not r["상품명"].startswith("(예시)") for r in rows):
            return
    write_template(path)


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
                    note=(row.get("메모") or "").strip(),
                )
            )
    return releases
