from stockscreener.data.provider import DataUnavailableError
from stockscreener.models import PriceSnapshot, YearlyFinancials
from stockscreener.screener import Screener


def _healthy_years(ticker):
    years = []
    for i in range(6):
        years.append(
            YearlyFinancials(
                fiscal_year=2015 + i,
                revenue=1000.0 + 100 * i,
                net_income=100.0 + 20 * i,
                eps=1.0 + 0.2 * i,
                book_value_per_share=10.0 + i,
                total_current_assets=500.0,
                total_current_liabilities=200.0,
                total_liabilities=600.0,
                long_term_debt=100.0,
                dividend_per_share=0.5,
            )
        )
    return years


class FakeDataProvider:
    def __init__(self, prices, financials):
        self.prices = prices
        self.financials = financials

    def get_price_snapshot(self, ticker):
        if ticker not in self.prices:
            raise DataUnavailableError(f"{ticker}: 가짜 데이터 없음")
        return self.prices[ticker]

    def get_annual_financials(self, ticker, max_years=10):
        return self.financials.get(ticker, [])


class FakeNewsProvider:
    def __init__(self):
        self.market_calls = 0
        self.ticker_calls = []

    def get_market_news(self):
        self.market_calls += 1
        return []

    def get_ticker_news(self, ticker, company_name=None):
        self.ticker_calls.append(ticker)
        return []


def test_analyze_ticker_success_produces_full_report():
    provider = FakeDataProvider(
        prices={"CHEAP": PriceSnapshot(ticker="CHEAP", price=5.0, currency="USD")},
        financials={"CHEAP": _healthy_years("CHEAP")},
    )
    screener = Screener(data_provider=provider, news_provider=FakeNewsProvider())
    report = screener.analyze_ticker("CHEAP")

    assert report.ok
    assert report.trend is not None
    assert report.graham is not None
    assert report.valuation is not None
    assert report.valuation.is_undervalued is True


def test_analyze_ticker_isolates_failure_and_continues_batch():
    provider = FakeDataProvider(
        prices={"OK": PriceSnapshot(ticker="OK", price=50.0, currency="USD")},
        financials={"OK": _healthy_years("OK")},
    )
    screener = Screener(data_provider=provider, news_provider=FakeNewsProvider())
    reports = screener.analyze(["BROKEN", "OK"])

    broken, ok = reports
    assert broken.ok is False
    assert "가짜 데이터 없음" in broken.error
    assert ok.ok is True


def test_analyze_ticker_handles_empty_financials_without_crashing():
    provider = FakeDataProvider(
        prices={"NOFIN": PriceSnapshot(ticker="NOFIN", price=50.0, currency="USD")},
        financials={},
    )
    screener = Screener(data_provider=provider, news_provider=FakeNewsProvider())
    report = screener.analyze_ticker("NOFIN")

    assert report.ok
    assert report.trend.insufficient_data is True
    assert report.valuation.is_undervalued is False


def test_undervalued_sorted_by_margin_of_safety_desc():
    provider = FakeDataProvider(
        prices={
            "A": PriceSnapshot(ticker="A", price=5.0, currency="USD"),
            "B": PriceSnapshot(ticker="B", price=8.0, currency="USD"),
        },
        financials={"A": _healthy_years("A"), "B": _healthy_years("B")},
    )
    screener = Screener(data_provider=provider, news_provider=FakeNewsProvider())
    reports = screener.analyze(["A", "B"])
    undervalued = screener.undervalued(reports)

    assert [r.ticker for r in undervalued] == ["A", "B"]
    assert undervalued[0].valuation.margin_of_safety >= undervalued[1].valuation.margin_of_safety


def test_market_news_failure_returns_empty_list_not_exception():
    class BrokenNewsProvider:
        def get_market_news(self):
            raise RuntimeError("network down")

        def get_ticker_news(self, ticker, company_name=None):
            raise RuntimeError("network down")

    provider = FakeDataProvider(prices={}, financials={})
    screener = Screener(data_provider=provider, news_provider=BrokenNewsProvider())
    assert screener.get_market_news() == []
