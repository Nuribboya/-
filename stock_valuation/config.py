from __future__ import annotations

import pandas as pd

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

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
        tables = pd.read_html(WIKI_SP500_URL)
        table = tables[0][["Symbol", "GICS Sector"]].rename(
            columns={"Symbol": "ticker", "GICS Sector": "sector"}
        )
        table["ticker"] = table["ticker"].str.replace(".", "-", regex=False)
    except Exception:
        table = FALLBACK_UNIVERSE.copy()

    if limit is not None:
        table = table.head(limit)
    return table.reset_index(drop=True)
