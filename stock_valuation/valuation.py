from __future__ import annotations

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


def compute_valuation_multiples(
    latest_price: pd.DataFrame, eps_book: pd.DataFrame, sector_map: pd.DataFrame
) -> pd.DataFrame:
    """P/E and P/B computed from raw price + as-filed EPS/book value — pure
    arithmetic on observed numbers, no analyst estimate involved."""
    df = latest_price.merge(eps_book, on="ticker").merge(sector_map, on="ticker")
    df["pe_ratio"] = df["close"] / df["trailing_eps"]
    df["pb_ratio"] = df["close"] / df["book_value_per_share"]
    return df


def add_cheapness_percentile(df: pd.DataFrame) -> pd.DataFrame:
    """Rank each name's P/E and P/B against same-sector peers; 0 = cheapest."""
    out = df.copy()
    for col in ["pe_ratio", "pb_ratio"]:
        valid = out[col] > 0  # negative P/E (losses) isn't a valuation signal
        out[f"{col}_pct"] = pd.NA
        out.loc[valid, f"{col}_pct"] = out.loc[valid].groupby("sector")[col].rank(pct=True)
    out["cheapness_percentile"] = out[["pe_ratio_pct", "pb_ratio_pct"]].mean(axis=1, skipna=True)
    return out


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
