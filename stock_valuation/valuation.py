from __future__ import annotations

import numpy as np
import pandas as pd

# Cheapness percentile cutoffs -> staged buy tier. Lower percentile = cheaper
# vs. sector peers. Only applied to names that already cleared the quality
# gate below, so "cheap" never means "cheap because it's deteriorating".
BUY_TIERS = [
    (0.10, "3차 매수 (강한 저평가)"),
    (0.25, "2차 매수"),
    (0.40, "1차 매수"),
]
NO_SIGNAL = "관망"
QUALITY_GATE_PERCENTILE = 0.70  # must be in the top 30% of quality_score


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def compute_valuation_multiples(
    latest_price: pd.DataFrame, eps_book: pd.DataFrame, sector_map: pd.DataFrame
) -> pd.DataFrame:
    """P/E and P/B computed from raw price + as-filed EPS/book value — pure
    arithmetic on observed numbers, no analyst estimate involved."""
    df = latest_price.merge(eps_book, on="ticker").merge(sector_map, on="ticker")
    df["pe_ratio"] = df["close"] / df["trailing_eps"]
    df["pb_ratio"] = df["close"] / df["book_value_per_share"]
    return df


def add_cheapness_percentile(df: pd.DataFrame, group_cols: tuple[str, ...] = ("sector",)) -> pd.DataFrame:
    """Rank each name's P/E and P/B against peers sharing `group_cols`; 0 = cheapest.

    Pass group_cols=("period", "sector") for a historical, point-in-time
    series instead of a single latest-snapshot ranking.
    """
    out = df.copy()
    for col in ["pe_ratio", "pb_ratio"]:
        valid = out[col] > 0  # negative P/E (losses) isn't a valuation signal
        out[f"{col}_pct"] = pd.NA
        out.loc[valid, f"{col}_pct"] = out.loc[valid].groupby(list(group_cols))[col].rank(pct=True)
    out["cheapness_percentile"] = out[["pe_ratio_pct", "pb_ratio_pct"]].mean(axis=1, skipna=True)
    return out


def compute_point_in_time_multiples(
    fundamentals: pd.DataFrame, price_df: pd.DataFrame, sector_map: pd.DataFrame
) -> pd.DataFrame:
    """Historical P/E and P/B at each fundamentals period.

    Derived entirely from data already collected — no new fetches: trailing
    twelve-month EPS from the last 4 quarters' net income, book value per
    share from that period's equity, and price via `.asof()` so nothing
    here looks past the period it's dated for. Needed to build a
    point-in-time valuation-gap history (e.g. for the RL buy-timing
    backtest), since compute_valuation_multiples() only handles a single
    latest snapshot.
    """
    df = fundamentals.sort_values(["ticker", "period"]).copy()
    grouped = df.groupby("ticker", group_keys=False)
    df["ttm_net_income"] = grouped["net_income"].apply(lambda s: s.rolling(4, min_periods=4).sum())
    df["trailing_eps"] = _safe_div(df["ttm_net_income"], df["shares_outstanding"])
    df["book_value_per_share"] = _safe_div(df["total_equity"], df["shares_outstanding"])

    prices_by_ticker = {
        ticker: g.sort_values("date").set_index("date")["close"] for ticker, g in price_df.groupby("ticker")
    }
    df["price"] = [
        prices_by_ticker[t].asof(p) if t in prices_by_ticker else float("nan")
        for t, p in zip(df["ticker"], df["period"])
    ]

    df["pe_ratio"] = _safe_div(df["price"], df["trailing_eps"])
    df["pb_ratio"] = _safe_div(df["price"], df["book_value_per_share"])
    return df.merge(sector_map, on="ticker", how="left")[
        ["ticker", "period", "sector", "price", "pe_ratio", "pb_ratio"]
    ]


def assign_buy_tier(cheapness_percentile: float, quality_score: float, quality_cutoff: float) -> str:
    if pd.isna(cheapness_percentile) or quality_score < quality_cutoff:
        return NO_SIGNAL
    for threshold, tier in BUY_TIERS:
        if cheapness_percentile <= threshold:
            return tier
    return NO_SIGNAL


def build_valuation_signal(df: pd.DataFrame) -> pd.DataFrame:
    """df must have: ticker, sector, quality_score, cheapness_percentile."""
    out = df.copy()
    quality_cutoff = out["quality_score"].quantile(QUALITY_GATE_PERCENTILE)
    out["buy_tier"] = out.apply(
        lambda r: assign_buy_tier(r["cheapness_percentile"], r["quality_score"], quality_cutoff),
        axis=1,
    )
    return out.sort_values(["quality_score", "cheapness_percentile"], ascending=[False, True])
