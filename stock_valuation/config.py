from __future__ import annotations

import io

import pandas as pd
import requests

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# Wikipedia blocks the default urllib/pandas user agent as a bot; a normal
# browser UA gets through. Without this, read_html would fail every time
# and silently drop to the 10-name FALLBACK_UNIVERSE below.
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-valuation-screener/1.0)"}

# Small offline fallback so the pipeline still runs without network access.
# Real runs should use get_sp500_universe(), which pulls the live constituent
# list (with GICS sector) straight from Wikipedia's public disclosure table.
FALLBACK_UNIVERSE = pd.DataFrame(
    [
        ("AAPL", "Information Technology"),
        ("MSFT", "Information Technology"),
        ("NVDA", "Information Technology"),
        ("GOOGL", "Communication Services"),
        ("AMZN", "Consumer Discretionary"),
        ("META", "Communication Services"),
        ("JPM", "Financials"),
        ("JNJ", "Health Care"),
        ("XOM", "Energy"),
        ("PG", "Consumer Staples"),
    ],
    columns=["ticker", "sector"],
)


def get_sp500_universe(limit: int | None = None) -> pd.DataFrame:
    """Return a (ticker, sector) table for the current S&P500 constituents.

    Falls back to a small static sample when Wikipedia can't be reached, so
    the rest of the pipeline stays runnable offline for testing.
    """
    try:
        response = requests.get(WIKI_SP500_URL, headers=REQUEST_HEADERS, timeout=10)
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))
        table = tables[0][["Symbol", "GICS Sector"]].rename(
            columns={"Symbol": "ticker", "GICS Sector": "sector"}
        )
        table["ticker"] = table["ticker"].str.replace(".", "-", regex=False)
    except Exception as exc:
        print(f"[config] couldn't fetch live S&P500 list from Wikipedia ({exc!r}); using the 10-name fallback")
        table = FALLBACK_UNIVERSE.copy()

    if limit is not None:
        table = table.head(limit)
    return table.reset_index(drop=True)
