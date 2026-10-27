from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape

from resell_radar.models import DRAW, FCFS, KST, UNKNOWN, Release, Venue

# luck-d (럭드) gathers, per product, every Korean shop selling it, with the
# shop's own entry link and window. Its robots.txt allows these pages
# (/api/ and /search are the disallowed paths), and its terms have no
# scraping clause. Pages are server-rendered HTML.
SITE = "https://www.luck-d.com"
LIST_URL = SITE + "/release/?category={category}&page={page}"
PRODUCT_URL = SITE + "/products/{pid}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
# The list runs from today forward and is dominated by accessories, so read
# the footwear and clothing categories a few pages deep instead of "all".
CATEGORIES = {1: 8, 2: 3}  # category id -> pages
LIST_PAGES = sum(CATEGORIES.values())
REFRESH_SOON = timedelta(hours=6)  # re-read items releasing within a few days this often
REFRESH_LATER = timedelta(hours=24)

# The page carries a generated summary sentence with the key facts, e.g.
# "...의 발매 정보입니다. 발매일은 2026년 10월 28일, 제품 코드는 LB2808, 발매가는 69,000 KRW, ..."
SUMMARY = re.compile(r'class="product_info_content">\s*([^<]*발매 정보입니다[^<]*)<')
NAME_RE = re.compile(r"^(.+?)의 발매 정보입니다")
DATE_RE = re.compile(r"발매일은 (\d{4})년 (\d{1,2})월 (\d{1,2})일")
CODE_RE = re.compile(r"제품 코드는 ([^,]+),")
PRICE_RE = re.compile(r"발매가는 ([\d,]+) KRW")
SECTION_OR_CARD = re.compile(r'<h2[^>]*>([^<]*)</h2>|class="release__container__card"')
TITLE_RE = re.compile(r"<title>([^<|]+)\|[^<]*?(\d{4})년 (\d{1,2})월 (\d{1,2})일")
BUY_PRICE_RE = re.compile(r"바로 구매 · ([\d,]+) KRW")
# Only these page sections list where *this* product sells; the rest are
# recommendations for other products.
VENUE_SECTIONS = ("응모", "선착순", "판매")
SKIP_SECTIONS = ("비슷한", "다가오는", "최근 발매")
WHEN_RE = re.compile(r"(\d{1,2})월 (\d{1,2})일\s*(\d{1,2}):(\d{2})")
DRAW_WORDS = ("응모", "드로우", "래플", "추첨")


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_list(html: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'href="/products/(\d+)"', html)))


def _real_url(url: str) -> str:
    # Affiliate redirects carry the shop URL in "tu"; link straight to the shop.
    url = unescape(url)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return q["tu"][0] if "tu" in q else url


def _when(text: str, year: int, month: int) -> list[str]:
    """'10월 27일 10:00 ~ 10월 28일 23:59' -> ISO times; the year comes from the release date."""
    out = []
    for mo, d, h, mi in WHEN_RE.findall(text):
        y = year
        if int(mo) == 12 and month == 1:  # entry in December for a January release
            y -= 1
        elif int(mo) == 1 and month == 12:
            y += 1
        try:
            out.append(datetime(y, int(mo), int(d), int(h), int(mi), tzinfo=KST).isoformat())
        except ValueError:
            continue
    return out


def parse_product(html: str, pid: str) -> dict | None:
    m = SUMMARY.search(html)
    summary = _strip(m.group(1)) if m else ""
    name = NAME_RE.search(summary)
    date = DATE_RE.search(summary)
    code = CODE_RE.search(summary)
    price = PRICE_RE.search(summary) or BUY_PRICE_RE.search(html)
    if not (name and date):
        # Some categories (eyewear etc.) have no summary sentence; the title
        # still reads "<name> | 2026년 10월 29일 발매·가격·응모 - 럭드".
        t = TITLE_RE.search(html)
        if not t:
            return None
        name = name or re.match(r"(.+)", _strip(t.group(1)))
        date = date or t
        if not code:
            code = re.search(r"(\S+)$", _strip(t.group(1)))
    year = int(date.group(1) if date.re is DATE_RE else date.group(2))
    month = int(date.group(2) if date.re is DATE_RE else date.group(3))
    day = int(date.group(3) if date.re is DATE_RE else date.group(4))

    venues: dict[str, dict] = {}
    section = ""
    marks = list(SECTION_OR_CARD.finditer(html))
    for i, mk in enumerate(marks):
        if mk.group(1) is not None:
            section = _strip(mk.group(1))
            continue
        if any(w in section for w in SKIP_SECTIONS) or not any(w in section for w in VENUE_SECTIONS):
            continue
        chunk = html[mk.end(): marks[i + 1].start() if i + 1 < len(marks) else mk.end() + 4000]
        title = re.search(r'button_card__contents_title">\s*<span>([^<]+)</span>', chunk)
        if not title:
            continue
        cnt = re.search(r'button_card__contents_cnt">\s*<span>([^<]*)</span>', chunk)
        cnt = _strip(cnt.group(1)) if cnt else ""
        badges = [_strip(b) for b in re.findall(r'custom_badge[^"]*">([^<]+)<', chunk)]
        link = re.search(r'class="release_link"[^>]*data-url="([^"]+)"', chunk) or re.search(r'data-url="([^"]+)"', chunk)
        times = _when(cnt, year, month)
        is_draw = any(w in section or w in cnt for w in DRAW_WORDS)
        start = end = None
        if len(times) >= 2:
            start, end = times[0], times[1]
        elif times:
            if "마감" in cnt:
                end = times[0]
            else:
                start = times[0]
        venue = {
            "name": _strip(title.group(1)),
            "url": _real_url(link.group(1)) if link else "",
            "method": DRAW if is_draw else FCFS,
            "start": start,
            "end": end,
            "online": "오프라인" not in badges,
            "overseas": "해외" in badges,
        }
        # A shop often shows up twice (e.g. drop time + regular sale); keep
        # one row per shop, preferring its raffle and then a timed drop.
        prev = venues.get(venue["name"])
        if prev is None or (venue["method"] == DRAW and prev["method"] != DRAW) or (
            prev["method"] == venue["method"] and not (prev["start"] or prev["end"]) and (start or end)
        ):
            venues[venue["name"]] = venue
    return {
        "pid": pid,
        "name": name.group(1).strip(),
        # Date-only: keep the release listed through the end of its day.
        "date": datetime(year, month, day, 23, 59, tzinfo=KST).isoformat(),
        "code": code.group(1).strip() if code else "",
        "price": int(price.group(1).replace(",", "")) if price else None,
        "venues": list(venues.values()),
    }


def to_release(d: dict) -> Release:
    venues = [
        Venue(
            name=v["name"], url=v["url"], method=v["method"], online=v["online"], overseas=v["overseas"],
            start=datetime.fromisoformat(v["start"]) if v["start"] else None,
            end=datetime.fromisoformat(v["end"]) if v["end"] else None,
        )
        for v in d["venues"]
    ]
    kr = [v for v in venues if not v.overseas]
    draws = [v for v in kr if v.method == DRAW]
    starts = [v.start for v in (draws or kr) if v.start]
    ends = [v.end for v in draws if v.end]
    return Release(
        source="luckd",
        source_id=d["pid"],
        name=d["name"],
        brand=d["name"].split()[0] if d["name"] else "",
        retail_price=d["price"],
        release_at=datetime.fromisoformat(d["date"]) if d["date"] else None,
        entry_start=min(starts) if starts else None,
        entry_end=max(ends) if ends else None,
        method=DRAW if draws else FCFS if kr else UNKNOWN,
        retailer_count=len(kr) or None,
        url=PRODUCT_URL.format(pid=d["pid"]),
        style_code=d["code"],
        venues=venues,
    )


def _get(url: str, timeout: float) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch(store, get=None, pause: float = 0.5, timeout: float = 20) -> list[Release]:
    """Read the newest list pages, then product pages that are new or due a refresh.

    Parsed products are cached in the local DB so each run only loads what
    changed — a few list pages plus a handful of product pages.
    """
    get = get or (lambda url: _get(url, timeout))
    now = datetime.now(timezone.utc)
    ids: list[str] = []
    for category, pages in CATEGORIES.items():
        for page in range(1, pages + 1):
            try:
                html = get(LIST_URL.format(category=category, page=page))
            except urllib.error.HTTPError:  # past the last page
                break
            new = [i for i in parse_list(html) if i not in ids]
            if not new:
                break
            ids += new

    releases = []
    for pid in ids:
        cached = store.luckd_cached(pid)
        d = None
        if cached:
            data, fetched_at = cached
            d = json.loads(data)
            when = datetime.fromisoformat(d["date"]) if d.get("date") else None
            soon = when is not None and when - now < timedelta(days=3)
            if now - fetched_at > (REFRESH_SOON if soon else REFRESH_LATER):
                d = None
        if d is None:
            if pause:
                time.sleep(pause)
            try:
                html = get(PRODUCT_URL.format(pid=pid))
            except urllib.error.HTTPError:  # removed product
                html = ""
            d = (parse_product(html, pid) if html else None) or {"pid": pid, "unreadable": True}
            # Unreadable pages are cached too, so they aren't re-fetched every run.
            store.luckd_cache(pid, json.dumps(d, ensure_ascii=False))
        if d.get("unreadable"):
            continue
        releases.append(to_release(d))
    return releases
