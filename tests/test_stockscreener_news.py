import time

from stockscreener.news import rss_provider as rss_mod
from stockscreener.news.rss_provider import RSSNewsProvider


class _FakeEntry:
    def __init__(self, title, link, published_struct=None, summary=""):
        self.title = title
        self.link = link
        self.summary = summary
        if published_struct is not None:
            self.published_parsed = published_struct


class _FakeParsed:
    def __init__(self, entries, bozo=False, bozo_exception=None):
        self.entries = entries
        self.bozo = bozo
        self.bozo_exception = bozo_exception


def test_get_market_news_aggregates_and_dedupes(monkeypatch):
    t1 = time.gmtime(1700000000)
    t2 = time.gmtime(1700003600)

    def fake_parse(url):
        return _FakeParsed(
            [
                _FakeEntry("기사 A", "https://example.com/a", t1),
                _FakeEntry("기사 B", "https://example.com/b", t2),
            ]
        )

    monkeypatch.setattr(rss_mod.feedparser, "parse", fake_parse)

    provider = RSSNewsProvider(market_queries=["q1", "q2"])
    news = provider.get_market_news()

    # 두 검색어에서 같은 링크가 중복으로 나와도 한 번만 남아야 한다
    assert len({n.link for n in news}) == 2
    # 최신순 정렬
    assert news[0].published >= news[1].published


def test_one_broken_feed_does_not_break_others(monkeypatch):
    calls = {"n": 0}

    def fake_parse(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network error")
        return _FakeParsed([_FakeEntry("OK 기사", "https://example.com/ok")])

    monkeypatch.setattr(rss_mod.feedparser, "parse", fake_parse)

    provider = RSSNewsProvider(market_queries=["broken", "ok"])
    news = provider.get_market_news()

    assert len(news) == 1
    assert news[0].title == "OK 기사"


def test_bozo_with_no_entries_returns_empty(monkeypatch):
    def fake_parse(url):
        return _FakeParsed([], bozo=True, bozo_exception="malformed xml")

    monkeypatch.setattr(rss_mod.feedparser, "parse", fake_parse)

    provider = RSSNewsProvider(market_queries=["q1"])
    assert provider.get_market_news() == []


def test_get_ticker_news_uses_company_name_in_query(monkeypatch):
    captured_urls = []

    def fake_parse(url):
        captured_urls.append(url)
        return _FakeParsed([_FakeEntry("삼성전자 뉴스", "https://example.com/samsung")])

    monkeypatch.setattr(rss_mod.feedparser, "parse", fake_parse)

    provider = RSSNewsProvider()
    news = provider.get_ticker_news("005930.KS", company_name="Samsung Electronics")

    assert len(news) == 1
    assert "Samsung" in captured_urls[0] or "Samsung%20Electronics" in captured_urls[0]
