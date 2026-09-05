"""야후 파이낸스(yfinance) 기반 DataProvider 구현.

주의: 야후 파이낸스 무료 API는 연간 재무제표를 보통 최근 4~5개년치만
제공한다. 10개년 전체 이력이 필요하면 이 모듈의 DataProvider 프로토콜에
맞춰 유료 데이터 공급자(예: Financial Modeling Prep)를 별도로 구현해
`Screener(provider=...)` 에 주입하면 된다. 이 모듈은 외부 데이터의 스키마가
버전에 따라 바뀔 수 있다는 전제로, 필드를 찾지 못하면 예외를 던지는 대신
None으로 채워 상위 분석 로직이 "데이터 부족"으로 처리하게 한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd
import yfinance as yf

from stockscreener.data.provider import DataUnavailableError
from stockscreener.models import PriceSnapshot, YearlyFinancials

logger = logging.getLogger(__name__)

_REVENUE_ROWS = ["Total Revenue", "TotalRevenue", "Revenue", "OperatingRevenue"]
_NET_INCOME_ROWS = [
    "Net Income Common Stockholders",
    "NetIncomeCommonStockholders",
    "Net Income",
    "NetIncome",
]
_EPS_ROWS = ["Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS"]
_CURRENT_ASSETS_ROWS = ["Current Assets", "CurrentAssets", "Total Current Assets", "TotalCurrentAssets"]
_CURRENT_LIABILITIES_ROWS = [
    "Current Liabilities",
    "CurrentLiabilities",
    "Total Current Liabilities",
    "TotalCurrentLiabilities",
]
_TOTAL_LIABILITIES_ROWS = [
    "Total Liabilities Net Minority Interest",
    "TotalLiabilitiesNetMinorityInterest",
    "Total Liab",
    "TotalLiab",
]
_LONG_TERM_DEBT_ROWS = [
    "Long Term Debt",
    "LongTermDebt",
    "Long Term Debt And Capital Lease Obligation",
]
_EQUITY_ROWS = [
    "Common Stock Equity",
    "CommonStockEquity",
    "Stockholders Equity",
    "StockholdersEquity",
    "Total Stockholder Equity",
]
_SHARES_ROWS = ["Ordinary Shares Number", "OrdinarySharesNumber", "Share Issued", "ShareIssued"]

_PRICE_KEYS = ["last_price", "lastPrice", "regularMarketPrice", "currentPrice"]
_SHARES_KEYS = ["shares", "sharesOutstanding", "shares_outstanding"]
_CURRENCY_KEYS = ["currency"]


def _find_row(df: Optional[pd.DataFrame], candidates: list[str]) -> Optional[pd.Series]:
    if df is None or df.empty:
        return None
    index_lookup = {str(idx).strip().lower(): idx for idx in df.index}
    for name in candidates:
        key = name.strip().lower()
        if key in index_lookup:
            return df.loc[index_lookup[key]]
    return None


def _cell(row: Optional[pd.Series], col: Any) -> Optional[float]:
    if row is None or col not in row.index:
        return None
    value = row[col]
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_attr(obj: Any, keys: list[str]) -> Optional[Any]:
    for key in keys:
        try:
            value = obj[key]
        except Exception:
            try:
                value = getattr(obj, key)
            except Exception:
                value = None
        if value not in (None, ""):
            return value
    return None


class YFinanceProvider:
    def __init__(self, ticker_factory: Callable[[str], Any] = yf.Ticker):
        self._ticker_factory = ticker_factory

    def get_price_snapshot(self, ticker: str) -> PriceSnapshot:
        try:
            t = self._ticker_factory(ticker)
        except Exception as exc:
            raise DataUnavailableError(f"{ticker}: 종목 조회 실패 ({exc})") from exc

        price = None
        shares = None
        currency = None

        try:
            fast = t.fast_info
            price = _first_attr(fast, _PRICE_KEYS)
            shares = _first_attr(fast, _SHARES_KEYS)
            currency = _first_attr(fast, _CURRENCY_KEYS)
        except Exception as exc:
            logger.warning("%s: fast_info 조회 실패 (%s)", ticker, exc)

        if price is None:
            try:
                info = t.info or {}
                price = _first_attr(info, _PRICE_KEYS)
                if shares is None:
                    shares = _first_attr(info, _SHARES_KEYS)
                if currency is None:
                    currency = _first_attr(info, _CURRENCY_KEYS)
            except Exception as exc:
                logger.warning("%s: info 조회 실패 (%s)", ticker, exc)

        if price is None:
            raise DataUnavailableError(f"{ticker}: 현재가를 확인할 수 없습니다")

        return PriceSnapshot(
            ticker=ticker,
            price=float(price),
            currency=str(currency) if currency else "USD",
            shares_outstanding=float(shares) if shares else None,
            as_of=datetime.now(timezone.utc),
        )

    def get_annual_financials(self, ticker: str, max_years: int = 10) -> list[YearlyFinancials]:
        try:
            t = self._ticker_factory(ticker)
            income = t.get_income_stmt(freq="yearly")
            balance = t.get_balance_sheet(freq="yearly")
        except Exception as exc:
            logger.warning("%s: 재무제표 조회 실패 (%s)", ticker, exc)
            return []

        if income is None or income.empty:
            return []

        revenue_row = _find_row(income, _REVENUE_ROWS)
        net_income_row = _find_row(income, _NET_INCOME_ROWS)
        eps_row = _find_row(income, _EPS_ROWS)
        current_assets_row = _find_row(balance, _CURRENT_ASSETS_ROWS)
        current_liabilities_row = _find_row(balance, _CURRENT_LIABILITIES_ROWS)
        total_liabilities_row = _find_row(balance, _TOTAL_LIABILITIES_ROWS)
        long_term_debt_row = _find_row(balance, _LONG_TERM_DEBT_ROWS)
        equity_row = _find_row(balance, _EQUITY_ROWS)
        shares_row = _find_row(balance, _SHARES_ROWS)

        dividends_by_year: dict[int, float] = {}
        try:
            dividends = t.dividends
            if dividends is not None and not dividends.empty:
                for ts, amount in dividends.items():
                    year = pd.Timestamp(ts).year
                    dividends_by_year[year] = dividends_by_year.get(year, 0.0) + float(amount)
        except Exception as exc:
            logger.warning("%s: 배당 이력 조회 실패 (%s)", ticker, exc)

        try:
            columns = sorted(income.columns, key=lambda c: pd.Timestamp(c))
        except Exception:
            columns = list(income.columns)
        columns = columns[-max_years:] if max_years else columns

        years: list[YearlyFinancials] = []
        for col in columns:
            try:
                fiscal_year = pd.Timestamp(col).year
            except Exception:
                continue

            equity = _cell(equity_row, col)
            shares = _cell(shares_row, col)
            book_value_per_share = (
                equity / shares if equity is not None and shares else None
            )

            years.append(
                YearlyFinancials(
                    fiscal_year=fiscal_year,
                    revenue=_cell(revenue_row, col),
                    net_income=_cell(net_income_row, col),
                    eps=_cell(eps_row, col),
                    book_value_per_share=book_value_per_share,
                    total_current_assets=_cell(current_assets_row, col),
                    total_current_liabilities=_cell(current_liabilities_row, col),
                    total_liabilities=_cell(total_liabilities_row, col),
                    long_term_debt=_cell(long_term_debt_row, col),
                    dividend_per_share=dividends_by_year.get(fiscal_year),
                )
            )
        return years
