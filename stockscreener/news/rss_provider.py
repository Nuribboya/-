"""RSS 기반 뉴스 공급자.

호출할 때마다 실시간으로 새로 가져오며 별도로 캐시하지 않는다(요청마다 갱신).
개별 피드 하나가 실패해도 나머지 피드는 계속 처리되도록 오류를 격리한다.
구글 뉴스 검색 RSS(`news.google.com/rss/search`)를 기본으로 사용하며, 이는
특정 언론사 RSS가 개편/폐지되어도 검색어 기반으로 계속 동작하는 안정적인
공개 엔드포인트다.
"""
from __future__ import annotations

import logging
import urllib.request
from datetime import datetime, timezone
from time import mktime
from typing import Iterable, Optional
from urllib.parse import quote

import feedparser

from stockscreener.models import NewsItem

logger = logging.getLogger(__name__)

# 증시 전반에 영향을 주는 기본 검색어 (거시경제/금리/인플레이션 등)
DEFAULT_MARKET_QUERIES = [
    "stock market",
    "federal reserve interest rate",
    "inflation economy",
]

# feedparser.parse(url)에 URL을 직접 넘기면 내부 urllib에 타임아웃이 걸리지 않아
# 네트워크가 응답 없이 멈출 경우 프로그램 전체가 무한정 멈출 수 있다. 직접
# urlopen으로 받아온 바이트를 넘겨 반드시 타임아웃이 걸리게 한다.
FETCH_TIMEOUT_SECONDS = 10.0


def _google_news_rss_url(query: str, lang: str, country: str) -> str:
    ceid = f"{country}:{lang.split('-')[0]}"
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={lang}&gl={country}&ceid={ceid}"


def _fetch_bytes(url: str, timeout: float = FETCH_TIMEOUT_SECONDS) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _parse_entries(feed_url: str, source_label: str, limit: int) -> list[NewsItem]:
    try:
        raw = _fetch_bytes(feed_url)
    except Exception as exc:
        logger.warning("뉴스 피드 조회 실패 (%s): %s", feed_url, exc)
        return []

    try:
        parsed = feedparser.parse(raw)
    except Exception as exc:
        logger.warning("뉴스 피드 파싱 실패 (%s): %s", feed_url, exc)
        return []

    if getattr(parsed, "bozo", False) and not parsed.entries:
        logger.warning(
            "뉴스 피드 파싱 실패 (%s): %s", feed_url, getattr(parsed, "bozo_exception", "")
        )
        return []

    items: list[NewsItem] = []
    for entry in parsed.entries[:limit]:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        if not title or not link:
            continue

        published = None
        parsed_time = getattr(entry, "published_parsed", None) or getattr(
            entry, "updated_parsed", None
        )
        if parsed_time:
            try:
                published = datetime.fromtimestamp(mktime(parsed_time), tz=timezone.utc)
            except (OverflowError, ValueError, TypeError):
                published = None

        items.append(
            NewsItem(
                title=title,
                link=link,
                source=source_label,
                published=published,
                summary=getattr(entry, "summary", None),
            )
        )
    return items


def _dedupe_sorted(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        if item.link in seen:
            continue
        seen.add(item.link)
        deduped.append(item)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    deduped.sort(key=lambda i: i.published or epoch, reverse=True)
    return deduped


class RSSNewsProvider:
    """구글 뉴스 검색 RSS로 시장/종목 뉴스를 실시간 조회한다."""

    def __init__(
        self,
        market_queries: Optional[Iterable[str]] = None,
        lang: str = "en-US",
        country: str = "US",
        per_feed_limit: int = 10,
    ):
        self.market_queries = (
            list(market_queries) if market_queries else list(DEFAULT_MARKET_QUERIES)
        )
        self.lang = lang
        self.country = country
        self.per_feed_limit = per_feed_limit

    def get_market_news(self) -> list[NewsItem]:
        """증시 전반에 영향을 주는 뉴스를 조회한다 (호출할 때마다 새로 조회)."""
        all_items: list[NewsItem] = []
        for query in self.market_queries:
            url = _google_news_rss_url(query, self.lang, self.country)
            all_items.extend(
                _parse_entries(url, source_label=f"news:{query}", limit=self.per_feed_limit)
            )
        return _dedupe_sorted(all_items)

    def get_ticker_news(self, ticker: str, company_name: Optional[str] = None) -> list[NewsItem]:
        """개별 종목 관련 뉴스를 조회한다 (호출할 때마다 새로 조회)."""
        query = company_name or ticker
        url = _google_news_rss_url(query, self.lang, self.country)
        items = _parse_entries(url, source_label=f"news:{query}", limit=self.per_feed_limit)
        return _dedupe_sorted(items)
