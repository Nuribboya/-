from __future__ import annotations

import pandas as pd


def compute_forward_returns(
    price_df: pd.DataFrame, periods: list[pd.Timestamp], horizon_days: int = 252
) -> pd.DataFrame:
    """Forward return per (period, ticker) over `horizon_days` calendar days.

    Uses `.asof()` (last known price at-or-before a date) on both ends so
    nothing here ever looks past the date it's dated for — no look-ahead.
    """
    rows = []
    for ticker, g in price_df.groupby("ticker"):
        series = g.sort_values("date").set_index("date")["close"]
        for period in periods:
            target = period + pd.Timedelta(days=horizon_days)
            if target > series.index.max():
                continue
            now_price = series.asof(period)
            future_price = series.asof(target)
            if pd.isna(now_price) or pd.isna(future_price) or now_price == 0:
                continue
            rows.append(
                {
                    "period": period,
                    "ticker": ticker,
                    "forward_return": future_price / now_price - 1,
                }
            )
    return pd.DataFrame(rows)


def add_relative_return_tiers(forward_returns: pd.DataFrame, n_tiers: int = 3) -> pd.DataFrame:
    """Bucket each period's cross-section into n_tiers by forward return.

    Labels are relative rank within the same period (0=bottom tier ...
    n_tiers-1=top tier), not absolute return — matching the "which
    long-term winners beat their peers" framing rather than price forecasting.
    """
    df = forward_returns.copy()

    def _tier(group: pd.Series) -> pd.Series:
        try:
            return pd.qcut(group, n_tiers, labels=False, duplicates="drop")
        except ValueError:
            return pd.Series(index=group.index, dtype=float)

    df["label"] = df.groupby("period")["forward_return"].transform(_tier)
    return df.dropna(subset=["label"])
