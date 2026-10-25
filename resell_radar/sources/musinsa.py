from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from resell_radar.models import DRAW, KST, Release

# Each raffle has a server-rendered page whose __NEXT_DATA__ carries the full
# raffle record (price, entry window, entry/winner counts). Raffle numbers are
# sequential, so new raffles are found by checking a few numbers past the
# highest one seen so far — a handful of page loads per run.
DETAIL_URL = "https://www.musinsa.com/events/raffle/{no}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
START_NO = 1278  # newest raffle as of 2026-10-26
LOOK_BACK, LOOK_AHEAD = 5, 10
ENDED = "래플 종료"
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def parse_detail(html: str) -> dict | None:
    m = NEXT_DATA.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data.get("props", {}).get("pageProps", {}).get("raffleData") or None


def _kst(value: str | None) -> datetime | None:
    # Times come without an offset and are Korean local time.
    try:
        return datetime.fromisoformat(value).replace(tzinfo=KST) if value else None
    except ValueError:
        return None


def to_release(d: dict) -> Release:
    price, normal = d.get("price"), d.get("normalPrice")
    note = ""
    if price and normal and price < normal:
        note = f"이벤트가 {price:,}원 (정가 {normal:,}원)"
    return Release(
        source="musinsa",
        source_id=str(d["raffleNo"]),
        name=d.get("title") or d.get("goodsName") or f"무신사 래플 {d['raffleNo']}",
        brand=f"무신사 래플 · {d.get('brandName') or ''}".strip(" ·"),
        retail_price=price or None,
        entry_start=_kst(d.get("entryStartTime")),
        entry_end=_kst(d.get("entryEndTime")),
        method=DRAW,
        url=DETAIL_URL.format(no=d["raffleNo"]),
        note=note,
        entries=d.get("entryCount"),
        winners=d.get("winnerCount"),
    )


def _get(no: int, timeout: float) -> str | None:
    req = urllib.request.Request(DETAIL_URL.format(no=no), headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError:
        return None


def fetch(last_no: int | None = None, get=None, timeout: float = 20) -> tuple[list[Release], int]:
    """Return (open raffles, highest raffle number seen)."""
    get = get or (lambda no: _get(no, timeout))
    last_no = last_no or START_NO
    now = datetime.now(timezone.utc) - timedelta(hours=1)
    releases, highest, misses = [], last_no, 0
    for no in range(last_no - LOOK_BACK, last_no + LOOK_AHEAD + 1):
        html = get(no)
        d = parse_detail(html) if html else None
        if d is None:
            if no > last_no:
                misses += 1
                if misses >= 4:  # numbers past the newest raffle don't exist yet
                    break
            continue
        misses = 0
        highest = max(highest, no)
        r = to_release(d)
        if d.get("raffleStateMessage") != ENDED and (r.entry_end is None or r.entry_end >= now):
            releases.append(r)
    return releases, highest
