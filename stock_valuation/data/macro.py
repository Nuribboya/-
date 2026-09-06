from __future__ import annotations

import pandas as pd
from pandas_datareader import data as pdr

# FRED series codes: observed economic statistics, not forecasts or opinions.
FRED_SERIES = {
    "DGS10": "treasury_10y",
    "CPIAUCSL": "cpi",
    "UNRATE": "unemployment_rate",
    "INDPRO": "industrial_production",
}


def fetch_macro_indicators(start: str, end: str | None = None) -> pd.DataFrame:
    """Fetch macro time series from FRED and resample to quarter-end, matching
    the cadence of quarterly fundamentals."""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    series = {}
    for code, name in FRED_SERIES.items():
        raw = pdr.DataReader(code, "fred", start, end)[code]
        series[name] = raw.resample("QE").last()

    out = pd.DataFrame(series)
    out.index.name = "period"
    return out
