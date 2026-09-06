from __future__ import annotations

import pandas as pd


def classify_revenue_consistency(
    annual_revenue: pd.DataFrame, min_years: int = 3, allowed_dip: float = 0.02
) -> tuple[bool, str]:
    """Has this company's own annual revenue grown without a real dip over
    the years actually available?

    Uses whatever depth yfinance's free annual financials return (often
    only ~4 years) rather than assuming a fixed multi-year window exists.
    A dip is only counted if revenue drops by more than `allowed_dip`
    (default 2%) year over year, so trivial noise doesn't disqualify an
    otherwise steady grower.
    """
    r = annual_revenue.dropna(subset=["revenue"]).sort_values("year")
    if len(r) < min_years:
        return False, f"연간 매출 데이터 부족 ({len(r)}개년)"

    revenues = r["revenue"].to_numpy()
    dips = sum(1 for i in range(1, len(revenues)) if revenues[i] < revenues[i - 1] * (1 - allowed_dip))
    total_growth = revenues[-1] / revenues[0] - 1 if revenues[0] > 0 else float("nan")

    if dips == 0 and total_growth > 0:
        return True, f"최근 {len(r)}개년 매출 꾸준히 증가 (누적 {total_growth * 100:.0f}%)"
    return False, f"최근 {len(r)}개년 중 매출 감소 {dips}회 발생"
