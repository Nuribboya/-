from __future__ import annotations

import json
import urllib.request
from datetime import datetime

from resell_radar.models import DRAW, FCFS, UNKNOWN, Release

# Public product feed the nike.com/kr/launch (SNKRS) page itself loads.
# channelId is the SNKRS web channel.
FEED_URL = (
    "https://api.nike.com/product_feed/threads/v3/"
    "?anchor={anchor}&count={count}"
    "&filter=marketplace(KR)&filter=language(ko)"
    "&filter=channelId(010794e5-35fe-4e32-aaff-cd2c74f89d61)"
    "&filter=exclusiveAccess(true,false)"
)
LAUNCH_URL = "https://www.nike.com/kr/launch/t/{slug}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}

# launchView.method: DAN = draw, LEO / FLOW = first come first served.
METHODS = {"DAN": DRAW, "LEO": FCFS, "FLOW": FCFS}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_feed(payload: dict) -> list[Release]:
    """Turn one feed page into Releases. Missing fields are skipped, not fatal."""
    releases = []
    for obj in payload.get("objects", []):
        props = obj.get("publishedContent", {}).get("properties", {})
        slug = props.get("seo", {}).get("slug", "")
        for info in obj.get("productInfo", []):
            merch = info.get("merchProduct", {})
            if merch.get("productType") not in (None, "FOOTWEAR", "APPAREL"):
                continue
            content = info.get("productContent", {})
            name = content.get("fullTitle") or content.get("title") or props.get("title")
            if not name:
                continue
            launch = info.get("launchView", {})
            price = info.get("merchPrice", {})
            retail = price.get("fullPrice") or price.get("currentPrice")
            releases.append(
                Release(
                    source="nike",
                    source_id=merch.get("styleColor") or merch.get("id") or name,
                    name=name,
                    brand="Nike",
                    retail_price=int(retail) if retail else None,
                    release_at=_parse_dt(merch.get("commerceStartDate")),
                    entry_start=_parse_dt(launch.get("startEntryDate")),
                    entry_end=_parse_dt(launch.get("stopEntryDate")),
                    method=METHODS.get(launch.get("method", ""), UNKNOWN),
                    # SNKRS is a single shop; other KR retailers aren't visible here.
                    retailer_count=None,
                    is_apparel=merch.get("productType") == "APPAREL",
                    style_code=merch.get("styleColor") or "",
                    url=LAUNCH_URL.format(slug=slug) if slug else "",
                )
            )
    return releases


def fetch(pages: int = 2, count: int = 50, timeout: float = 20) -> list[Release]:
    releases: list[Release] = []
    for page in range(pages):
        req = urllib.request.Request(FEED_URL.format(anchor=page * count, count=count), headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
        batch = parse_feed(payload)
        releases.extend(batch)
        if len(payload.get("objects", [])) < count:
            break
    return releases
