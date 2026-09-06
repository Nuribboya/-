from __future__ import annotations

import pandas as pd
import yfinance as yf


def fetch_price_history(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    """Fetch daily adjusted close + volume for one ticker. Pure market data,
    not an opinion — this is what actually traded, nothing modeled."""
    hist = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if hist.empty:
        return pd.DataFrame(columns=["date", "ticker", "close", "volume"])

    out = hist[["Close", "Volume"]].reset_index()
    out.columns = ["date", "close", "volume"]
    out["ticker"] = ticker
    return out[["date", "ticker", "close", "volume"]]


def fetch_trailing_eps_and_book(ticker: str) -> dict:
    """Trailing EPS and book value per share, used only to compute valuation
    multiples (P/E, P/B) — not fed to the fundamentals model itself."""
    info = yf.Ticker(ticker).info
    return {
        "ticker": ticker,
        "trailing_eps": info.get("trailingEps"),
        "book_value_per_share": info.get("bookValue"),
    }
