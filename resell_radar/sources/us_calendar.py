from __future__ import annotations

import hashlib
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape

from resell_radar.models import KST, Release

# US release-news RSS feeds. RSS is the format these sites publish for
# programs to read, so it is steadier than scraping their pages.
FEEDS = {
    "Sneaker News": "https://sneakernews.com/feed/",
    "Sneakerfiles": "https://www.sneakerfiles.com/feed/",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ResellRadar/1.0; +rss reader)"}
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
DATE_RE = re.compile(
    r"release date[s]?\s*[:\-–]?\s*(?:is\s+|on\s+)?"
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+)?"
    r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
    re.I,
)
PRICE_RE = re.compile(r"\$\s?(\d{2,4})(?:\.\d{2})?")
# Words that make a headline long but don't name the shoe.
TITLE_NOISE = re.compile(
    r"\b(release date|release info|releasing|releases|official images|official look|first look|"
    r"on[- ]feet|detailed look|where to buy|coming soon|is dropping|drops|returns|revealed|"
    r"unveil[s]?|arrive[s]?|available now)\b.*$",
    re.I,
)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


def parse_release_info(text: str) -> tuple[datetime | None, int | None]:
    """Find 'Release Date: November 5th, 2026' (+ a nearby '$210') in article text."""
    m = DATE_RE.search(text)
    if not m:
        return None, None
    month = MONTHS.get(m.group(1)[:3].lower())
    if not month:
        return None, None
    try:
        # US drops are usually 10am ET; on the KR calendar that is the same date
        # late at night, so the date itself is kept as the KR estimate.
        date = datetime(int(m.group(3)), month, int(m.group(2)), tzinfo=KST)
    except ValueError:
        return None, None
    price = PRICE_RE.search(text[m.end(): m.end() + 120]) or PRICE_RE.search(text[max(0, m.start() - 120): m.start()])
    return date, int(price.group(1)) if price else None


def clean_title(title: str) -> str:
    title = _text(title)
    cleaned = TITLE_NOISE.sub("", title).strip(" -–:,")
    return cleaned or title


def parse_feed(xml_text: str, site: str) -> list[Release]:
    root = ET.fromstring(xml_text)
    releases = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        body = item.findtext(CONTENT_NS) or item.findtext("description") or ""
        date, usd = parse_release_info(_text(body))
        if date is None:
            date, usd = parse_release_info(_text(title))
        if date is None:
            continue
        link = (item.findtext("link") or "").strip()
        name = clean_title(title)
        releases.append(
            Release(
                source="us_calendar",
                source_id=hashlib.sha1((link or name).encode()).hexdigest()[:12],
                name=name,
                brand=site,
                release_at=date,
                url=link,
                us_price=usd,
            )
        )
    return releases


def fetch(timeout: float = 20) -> tuple[list[Release], list[str]]:
    releases, errors = [], []
    now = datetime.now(timezone.utc) - timedelta(days=1)
    for site, url in FEEDS.items():
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
            releases.extend(r for r in parse_feed(xml_text, site) if r.release_at >= now)
        except Exception as exc:
            errors.append(f"{site} 피드 수집 실패: {exc}")
    return releases, errors
