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


def add_sector_relative_zscores(df: pd.DataFrame, sector_map: pd.DataFrame) -> pd.DataFrame:
    """Z-score each ratio within its (period, sector) peer group.

    Absolute ratio levels differ wildly across sectors (a bank's debt/equity
    means something different from a software company's), so comparing raw
    numbers across the whole universe is misleading — everything here is
    relative to same-period, same-sector peers instead.
    """
    out = df.merge(sector_map, on="ticker", how="left")
    for col in RATIO_COLUMNS:
        grouped = out.groupby(["period", "sector"])[col]
        mean = grouped.transform("mean")
        std = grouped.transform("std").replace(0, np.nan)
        out[f"{col}_z"] = (out[col] - mean) / std
    return out


def merge_macro(df: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
    return df.merge(macro, on="period", how="left")


def build_feature_table(
    fundamentals: pd.DataFrame, sector_map: pd.DataFrame, macro: pd.DataFrame
) -> pd.DataFrame:
    df = add_ratios(fundamentals)
    df = add_growth(df)
    df = add_sector_relative_zscores(df, sector_map)
    df = merge_macro(df, macro)
    return df


def feature_columns() -> list[str]:
    return [f"{col}_z" for col in RATIO_COLUMNS] + list(
        {"treasury_10y", "cpi", "unemployment_rate", "industrial_production"}
    )
