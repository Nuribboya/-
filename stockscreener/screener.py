"""전체 파이프라인을 조립하는 스크리너.

종목 하나의 조회/계산 오류가 전체 스크리닝을 중단시키지 않도록 종목별로
예외를 격리한다.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from stockscreener import config
from stockscreener.analysis.financial_trend import analyze_financial_trend
from stockscreener.analysis.graham import evaluate_graham_criteria
from stockscreener.analysis.valuation import evaluate_valuation
from stockscreener.data.provider import DataProvider, DataUnavailableError
from stockscreener.data.yfinance_provider import YFinanceProvider
from stockscreener.models import PriceSnapshot, StockReport
from stockscreener.news.rss_provider import RSSNewsProvider

logger = logging.getLogger(__name__)


def _empty_price(ticker: str) -> PriceSnapshot:
    return PriceSnapshot(ticker=ticker, price=None)


class Screener:
    def __init__(
        self,
        data_provider: Optional[DataProvider] = None,
        news_provider: Optional[RSSNewsProvider] = None,
        max_financial_years: int = config.MAX_FINANCIAL_YEARS,
        aaa_bond_yield_pct: float = config.DEFAULT_AAA_BOND_YIELD_PCT,
        min_margin_of_safety: float = config.DEFAULT_MIN_MARGIN_OF_SAFETY,
        news_per_ticker: int = config.DEFAULT_NEWS_PER_TICKER,
        fetch_ticker_news: bool = True,
    ):
        self.data_provider = data_provider or YFinanceProvider()
        self.news_provider = news_provider or RSSNewsProvider()
        self.max_financial_years = max_financial_years
        self.aaa_bond_yield_pct = aaa_bond_yield_pct
        self.min_margin_of_safety = min_margin_of_safety
        self.news_per_ticker = news_per_ticker
        self.fetch_ticker_news = fetch_ticker_news

    def analyze_ticker(self, ticker: str) -> StockReport:
        try:
            price_snapshot = self.data_provider.get_price_snapshot(ticker)
        except DataUnavailableError as exc:
            logger.warning("%s: 분석 중단 (%s)", ticker, exc)
            return StockReport(
                ticker=ticker, price=_empty_price(ticker), trend=None, graham=None,
                valuation=None, error=str(exc),
            )
        except Exception as exc:  # 예상 못한 오류도 전체 실행을 막지 않는다
            logger.exception("%s: 알 수 없는 오류", ticker)
            return StockReport(
                ticker=ticker, price=_empty_price(ticker), trend=None, graham=None,
                valuation=None, error=f"알 수 없는 오류: {exc}",
            )

        try:
            years = self.data_provider.get_annual_financials(ticker, self.max_financial_years)
        except Exception as exc:
            logger.warning("%s: 재무제표 조회 실패 (%s)", ticker, exc)
            years = []

        trend = analyze_financial_trend(years)
        graham = evaluate_graham_criteria(ticker, years, price_snapshot.price)

        latest = years[-1] if years else None
        eps = latest.eps if latest else None
        bvps = latest.book_value_per_share if latest else None
        valuation = evaluate_valuation(
            ticker=ticker,
            price=price_snapshot.price,
            eps=eps,
            book_value_per_share=bvps,
            trend=trend,
            aaa_bond_yield_pct=self.aaa_bond_yield_pct,
            min_margin_of_safety=self.min_margin_of_safety,
        )

        news: tuple = ()
        if self.fetch_ticker_news:
            try:
                news = tuple(self.news_provider.get_ticker_news(ticker)[: self.news_per_ticker])
            except Exception as exc:
                logger.warning("%s: 종목 뉴스 조회 실패 (%s)", ticker, exc)

        return StockReport(
            ticker=ticker, price=price_snapshot, trend=trend, graham=graham,
            valuation=valuation, news=news,
        )

    def analyze(self, tickers: Sequence[str]) -> list[StockReport]:
        return [self.analyze_ticker(t) for t in tickers]

    def get_market_news(self):
        try:
            return self.news_provider.get_market_news()
        except Exception as exc:
            logger.warning("시장 뉴스 조회 실패 (%s)", exc)
            return []

    def undervalued(self, reports: Sequence[StockReport]) -> list[StockReport]:
        candidates = [r for r in reports if r.ok and r.valuation and r.valuation.is_undervalued]
        candidates.sort(key=lambda r: r.valuation.margin_of_safety or 0.0, reverse=True)
        return candidates
