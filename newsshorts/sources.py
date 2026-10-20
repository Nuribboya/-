from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from newsshorts.config import Config, Source

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class NewsItem:
    id: str
    source: str
    title: str
    summary: str
    link: str
    published: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _clean(text: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", text or "")).split())


def _item_id(entry: dict) -> str:
    key = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def matches_keywords(item: NewsItem, keywords: list[str]) -> bool:
    if not keywords:
        return True
    text = f"{item.title} {item.summary}".lower()
    return any(k.lower() in text for k in keywords)


def parse_feed(source: Source, feed_text: str | bytes) -> list[NewsItem]:
    import feedparser

    parsed = feedparser.parse(feed_text)
    items = []
    for entry in parsed.entries:
        items.append(
            NewsItem(
                id=_item_id(entry),
                source=source.name,
                title=_clean(entry.get("title", "")),
                summary=_clean(entry.get("summary", "")),
                link=entry.get("link", ""),
                published=entry.get("published", ""),
            )
        )
    return items


def fetch_source(source: Source, timeout: float = 15.0) -> list[NewsItem]:
    import requests

    resp = requests.get(
        source.url,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; newsshorts/1.0)"},
    )
    resp.raise_for_status()
    return parse_feed(source, resp.content)


class SeenStore:
    """Remembers which items already became drafts so restarts don't repeat them."""

    def __init__(self, path: str | Path, limit: int = 5000):
        self.path = Path(path)
        self.limit = limit
        self._ids: list[str] = []
        if self.path.exists():
            self._ids = json.loads(self.path.read_text(encoding="utf-8"))

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._ids

    def add(self, item_id: str) -> None:
        if item_id not in self._ids:
            self._ids.append(item_id)
            self._ids = self._ids[-self.limit :]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._ids), encoding="utf-8")


def new_items(config: Config, seen: SeenStore, prime: bool = False) -> list[NewsItem]:
    """Fetch every source and return unseen items that pass the keyword filters.

    With prime=True every current item is marked seen and nothing is returned —
    used on first start so the backlog already in the feed isn't turned into videos.
    """
    fresh = []
    for source in config.sources:
        try:
            items = fetch_source(source)
        except Exception as exc:  # one broken feed shouldn't stop the others
            print(f"[warn] {source.name}: {exc}")
            continue
        for item in items:
            if item.id in seen:
                continue
            seen.add(item.id)
            if prime:
                continue
            if matches_keywords(item, source.keywords) and matches_keywords(item, config.keywords):
                fresh.append(item)
    seen.save()
    return fresh
