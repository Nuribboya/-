from __future__ import annotations

import pandas as pd
import yfinance as yf

# Only line items a company itself files, never a sell-side opinion. yfinance's
# `.info` dict also carries analyst fields (targetMeanPrice, recommendationKey,
# numberOfAnalystOpinions, ...) — those are deliberately never read here.
RAW_LINE_ITEMS = {
    "revenue": ["Total Revenue"],
    "operating_income": ["Operating Income"],
    "net_income": ["Net Income"],
    "total_debt": ["Total Debt"],
    "total_equity": ["Stockholders Equity", "Total Stockholder Equity"],
    "operating_cash_flow": ["Operating Cash Flow", "Total Cash From Operating Activities"],
    "capital_expenditure": ["Capital Expenditure"],
    "shares_outstanding": ["Ordinary Shares Number", "Share Issued"],
}


def _first_available_row(df: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def fetch_quarterly_fundamentals(ticker: str) -> pd.DataFrame:
    """Fetch raw, as-filed quarterly financial statement line items for one ticker.

    Returns a DataFrame indexed by period-end date with one column per item in
    RAW_LINE_ITEMS. Missing statements/items are left as NaN rather than
    guessed at.
    """
    t = yf.Ticker(ticker)
    income = t.quarterly_financials
    balance = t.quarterly_balance_sheet
    cashflow = t.quarterly_cashflow

    periods = sorted(set(income.columns) | set(balance.columns) | set(cashflow.columns))
    out = pd.DataFrame(index=periods)

    for field, candidates in RAW_LINE_ITEMS.items():
        for source in (income, balance, cashflow):
            row = _first_available_row(source, candidates)
            if row is not None:
                out[field] = row.reindex(periods)
                break
        else:
            out[field] = pd.NA

    out.index.name = "period"
    out["ticker"] = ticker
    return out.sort_index()
