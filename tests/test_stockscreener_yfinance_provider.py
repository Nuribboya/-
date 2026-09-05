import pandas as pd
import pytest

from stockscreener.data.provider import DataUnavailableError
from stockscreener.data.yfinance_provider import YFinanceProvider


class FakeTicker:
    def __init__(
        self, fast_info=None, info=None, income=None, balance=None, dividends=None, cashflow=None
    ):
        self.fast_info = fast_info if fast_info is not None else {}
        self.info = info if info is not None else {}
        self._income = income
        self._balance = balance
        self._cashflow = cashflow
        self.dividends = dividends if dividends is not None else pd.Series(dtype=float)

    def get_income_stmt(self, freq="yearly"):
        return self._income

    def get_balance_sheet(self, freq="yearly"):
        return self._balance

    def get_cash_flow(self, freq="yearly"):
        return self._cashflow


def _provider_for(fake_ticker):
    return YFinanceProvider(ticker_factory=lambda ticker: fake_ticker)


def test_get_price_snapshot_reads_fast_info():
    fake = FakeTicker(fast_info={"last_price": 123.45, "shares": 1000.0, "currency": "USD"})
    snapshot = _provider_for(fake).get_price_snapshot("FAKE")
    assert snapshot.price == 123.45
    assert snapshot.shares_outstanding == 1000.0
    assert snapshot.currency == "USD"


def test_get_price_snapshot_falls_back_to_info():
    fake = FakeTicker(fast_info={}, info={"currentPrice": 50.0, "sharesOutstanding": 500.0})
    snapshot = _provider_for(fake).get_price_snapshot("FAKE")
    assert snapshot.price == 50.0
    assert snapshot.shares_outstanding == 500.0


def test_get_price_snapshot_raises_when_price_unavailable():
    fake = FakeTicker(fast_info={}, info={})
    with pytest.raises(DataUnavailableError):
        _provider_for(fake).get_price_snapshot("FAKE")


def _sample_frames():
    cols = [pd.Timestamp("2019-12-31"), pd.Timestamp("2020-12-31")]
    income = pd.DataFrame(
        {
            cols[0]: [1000.0, 100.0, 1.0],
            cols[1]: [1200.0, 150.0, 1.5],
        },
        index=["Total Revenue", "Net Income", "Diluted EPS"],
    )
    balance = pd.DataFrame(
        {
            cols[0]: [500.0, 200.0, 600.0, 100.0, 800.0, 100.0],
            cols[1]: [550.0, 210.0, 650.0, 120.0, 900.0, 100.0],
        },
        index=[
            "Current Assets",
            "Current Liabilities",
            "Total Liabilities Net Minority Interest",
            "Long Term Debt",
            "Common Stock Equity",
            "Ordinary Shares Number",
        ],
    )
    return income, balance


def test_get_annual_financials_parses_known_row_labels():
    income, balance = _sample_frames()
    fake = FakeTicker(income=income, balance=balance)
    years = _provider_for(fake).get_annual_financials("FAKE", max_years=10)

    assert [y.fiscal_year for y in years] == [2019, 2020]
    first, second = years
    assert first.revenue == 1000.0
    assert first.net_income == 100.0
    assert first.eps == 1.0
    assert first.total_current_assets == 500.0
    assert first.total_current_liabilities == 200.0
    assert first.long_term_debt == 100.0
    assert first.book_value_per_share == 800.0 / 100.0
    assert second.book_value_per_share == 900.0 / 100.0
    assert first.total_equity == 800.0
    assert second.total_equity == 900.0
    assert first.debt_to_equity == 600.0 / 800.0
    assert first.free_cash_flow is None  # 이 테스트엔 현금흐름표가 없다


def test_get_annual_financials_parses_free_cash_flow_direct_row():
    income, balance = _sample_frames()
    cols = list(income.columns)
    cashflow = pd.DataFrame(
        {cols[0]: [120.0], cols[1]: [140.0]},
        index=["Free Cash Flow"],
    )
    fake = FakeTicker(income=income, balance=balance, cashflow=cashflow)
    years = _provider_for(fake).get_annual_financials("FAKE", max_years=10)
    assert years[0].free_cash_flow == 120.0
    assert years[1].free_cash_flow == 140.0


def test_get_annual_financials_computes_free_cash_flow_from_ocf_and_capex():
    income, balance = _sample_frames()
    cols = list(income.columns)
    cashflow = pd.DataFrame(
        {cols[0]: [150.0, -30.0], cols[1]: [180.0, -40.0]},
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    fake = FakeTicker(income=income, balance=balance, cashflow=cashflow)
    years = _provider_for(fake).get_annual_financials("FAKE", max_years=10)
    assert years[0].free_cash_flow == 150.0 - 30.0
    assert years[1].free_cash_flow == 180.0 - 40.0


def test_get_annual_financials_handles_missing_balance_sheet():
    income, _ = _sample_frames()
    fake = FakeTicker(income=income, balance=None)
    years = _provider_for(fake).get_annual_financials("FAKE", max_years=10)

    assert len(years) == 2
    assert years[0].revenue == 1000.0
    assert years[0].total_current_assets is None
    assert years[0].book_value_per_share is None


def test_get_annual_financials_returns_empty_when_income_missing():
    fake = FakeTicker(income=None, balance=None)
    years = _provider_for(fake).get_annual_financials("FAKE")
    assert years == []


def test_get_annual_financials_respects_max_years():
    income, balance = _sample_frames()
    fake = FakeTicker(income=income, balance=balance)
    years = _provider_for(fake).get_annual_financials("FAKE", max_years=1)
    assert len(years) == 1
    assert years[0].fiscal_year == 2020
