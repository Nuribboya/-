"""Decide which stories are big enough to publish without a human in the loop.

A story counts as "big" when the outlet itself flags it as breaking, or when
several independent sources report it within a short window. Auto-publishing
additionally requires a confirmed (non-speculative) report, a high audience
topic score, a Claude-written script that passed a fact check, and room under
the daily upload cap. Anything that fails a check stays a pending draft.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from newsshorts.audience import Audience, topic_score
from newsshorts.config import Config
from newsshorts.labels import is_rumor, report_type, title_labels
from newsshorts.sources import NewsItem

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "after", "over", "into", "about",
    "says", "said", "will", "has", "have", "are", "was", "were", "its", "his", "her", "their",
    "new", "news", "live", "update", "updates", "what", "how", "why", "who", "amid",
}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9가-힣]{3,}", text.lower()) if t not in _STOPWORDS}


def same_story(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb)
    return overlap >= 3 and overlap / len(ta | tb) >= 0.2


@dataclass
class Seen:
    id: str
    source: str
    title: str
    ts: float
    draft: str | None = None


class RecentStore:
    """Rolling window of recent headlines, used to spot multi-source stories."""

    def __init__(self, path: str | Path, window_minutes: int):
        self.path = Path(path)
        self.window = window_minutes * 60
        self.entries: list[Seen] = []
        if self.path.exists():
            self.entries = [Seen(**e) for e in json.loads(self.path.read_text(encoding="utf-8"))]

    def prune(self, now: float | None = None) -> None:
        now = now or time.time()
        self.entries = [e for e in self.entries if now - e.ts <= self.window]

    def add(self, item: NewsItem, now: float | None = None) -> None:
        if not any(e.id == item.id for e in self.entries):
            self.entries.append(Seen(item.id, item.source, item.title, now or time.time()))

    def story(self, item: NewsItem) -> list[Seen]:
        """Every recent entry (including the item itself) about the same story."""
        return [e for e in self.entries if e.id == item.id or same_story(e.title, item.title)]

    def source_count(self, item: NewsItem) -> int:
        return len({e.source for e in self.story(item)})

    def existing_draft(self, item: NewsItem) -> str | None:
        return next((e.draft for e in self.story(item) if e.draft and e.id != item.id), None)

    def mark_draft(self, item: NewsItem, draft: Path) -> None:
        for e in self.entries:
            if e.id == item.id:
                e.draft = str(draft)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([e.__dict__ for e in self.entries], ensure_ascii=False),
                             encoding="utf-8")


def is_big(item: NewsItem, recent: RecentStore, config: Config) -> bool:
    return "속보" in title_labels(item) or recent.source_count(item) >= config.auto_min_sources


def auto_publish_blockers(item: NewsItem, meta: dict, recent: RecentStore, config: Config,
                          audience: Audience, published_today: int) -> list[str]:
    """Reasons this draft must wait for a human; empty means it may go out automatically."""
    reasons = []
    if not config.auto_publish:
        reasons.append("auto_publish 꺼짐")
    if item.region not in config.auto_regions:
        reasons.append(f"자동 공개 대상 지역 아님({item.region})")
    if not is_big(item, recent, config):
        reasons.append(f"큰 소식 아님(속보 아님, 보도 매체 {recent.source_count(item)}곳)")
    if report_type(item) != "보도":
        reasons.append("전망·관측 기사")
    if is_rumor(item):
        reasons.append("소문 관련 기사")
    if topic_score(item, audience) < config.auto_min_topic_score:
        reasons.append("시청자 관심 주제 점수 낮음")
    if meta["script"].get("generator") != "claude":
        reasons.append("템플릿 대본(Claude 미사용)")
    check = meta.get("fact_check") or {}
    if not check.get("ok"):
        reasons.append("사실 확인 통과 못 함: " + "; ".join(check.get("problems", ["검사 안 됨"])))
    if published_today >= config.auto_daily_limit:
        reasons.append(f"오늘 자동 공개 한도({config.auto_daily_limit}개) 도달")
    return reasons
