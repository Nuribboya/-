from __future__ import annotations

import numpy as np
import pandas as pd

RATIO_COLUMNS = [
    "operating_margin",
    "net_margin",
    "roe",
    "debt_to_equity",
    "fcf_margin",
    "revenue_growth_yoy",
    "net_income_growth_yoy",
]


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denom = denominator.replace(0, np.nan)
    return numerator / denom


def add_ratios(fundamentals: pd.DataFrame) -> pd.DataFrame:
    df = fundamentals.copy()
    df["operating_margin"] = _safe_div(df["operating_income"], df["revenue"])
    df["net_margin"] = _safe_div(df["net_income"], df["revenue"])
    df["roe"] = _safe_div(df["net_income"], df["total_equity"])
    df["debt_to_equity"] = _safe_div(df["total_debt"], df["total_equity"])
    df["free_cash_flow"] = df["operating_cash_flow"] + df["capital_expenditure"]
    df["fcf_margin"] = _safe_div(df["free_cash_flow"], df["revenue"])
    return df


def add_growth(fundamentals: pd.DataFrame) -> pd.DataFrame:
    df = fundamentals.sort_values(["ticker", "period"]).copy()
    grouped = df.groupby("ticker", group_keys=False)
    df["revenue_growth_yoy"] = grouped["revenue"].apply(lambda s: s / s.shift(4) - 1)
    df["net_income_growth_yoy"] = grouped["net_income"].apply(lambda s: s / s.shift(4) - 1)
    return df


def add_sector_relative_zscores(
    df: pd.DataFrame, sector_map: pd.DataFrame, min_peer_group: int = 3
) -> pd.DataFrame:
    """Z-score each ratio within its (period, sector) peer group.

    Absolute ratio levels differ wildly across sectors (a bank's debt/equity
    means something different from a software company's), so comparing raw
    numbers across the whole universe is misleading — everything here is
    relative to same-period, same-sector peers instead.

    Falls back to the whole period's cross-section when a sector has fewer
    than `min_peer_group` names that period (small test universes, e.g.
    --limit 30, otherwise leave most sectors as single-member groups with an
    undefined std and turn the whole feature column to NaN).
    """
    out = df.merge(sector_map, on="ticker", how="left")
    out[RATIO_COLUMNS] = out[RATIO_COLUMNS].apply(pd.to_numeric, errors="coerce")
    for col in RATIO_COLUMNS:
        sector_group = out.groupby(["period", "sector"])[col]
        sector_size = sector_group.transform("size")
        sector_mean = sector_group.transform("mean")
        sector_std = sector_group.transform("std")

        period_group = out.groupby("period")[col]
        period_mean = period_group.transform("mean")
        period_std = period_group.transform("std")

        use_sector = sector_size >= min_peer_group
        mean = sector_mean.where(use_sector, period_mean)
        std = sector_std.where(use_sector, period_std).replace(0, np.nan)
        out[f"{col}_z"] = (out[col] - mean) / std
    return out


def merge_macro(df: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
    """Attach the macro snapshot nearest each fundamentals period.

    A company's fiscal quarter-end rarely lands on the exact same calendar
    date as FRED's resampled quarter-end, so an exact-match merge on
    `period` would silently leave every macro column NaN. `merge_asof`
    matches each row to its nearest macro date instead.
    """
    df_sorted = df.copy()
    df_sorted["period"] = pd.to_datetime(df_sorted["period"]).astype("datetime64[ns]")
    df_sorted = df_sorted.sort_values("period")

    macro_sorted = macro.reset_index()
    macro_sorted["period"] = pd.to_datetime(macro_sorted["period"]).astype("datetime64[ns]")
    macro_sorted = macro_sorted.sort_values("period")

    return pd.merge_asof(df_sorted, macro_sorted, on="period", direction="nearest")


def build_feature_table(
    fundamentals: pd.DataFrame, sector_map: pd.DataFrame, macro: pd.DataFrame
) -> pd.DataFrame:
    df = add_ratios(fundamentals)
    df = add_growth(df)
    df = add_sector_relative_zscores(df, sector_map)
    df = merge_macro(df, macro)
    return df


def latest_snapshot_per_ticker(df: pd.DataFrame) -> pd.DataFrame:
    """Each ticker's most recent row, independent of label availability.

    Used for live scoring: a forward-return label needs a full horizon of
    future price data, so the truly latest quarter never has one yet.
    Pulling "latest" from a label-joined dataset would silently drop every
    ticker's current quarter instead of scoring it.
    """
    return df.sort_values("period").groupby("ticker", as_index=False).tail(1)


def feature_columns() -> list[str]:
    return [f"{col}_z" for col in RATIO_COLUMNS] + list(
        {"treasury_10y", "cpi", "unemployment_rate", "industrial_production"}
    )
