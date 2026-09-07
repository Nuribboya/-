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


def classify_debt_health(
    ticker_history: pd.DataFrame, max_debt_to_equity: float = 2.0, max_increase: float = 0.5
) -> tuple[bool, str]:
    """Is this company's leverage under control, from its own quarterly
    debt/equity — no bond-rating opinion, just the raw ratio and how much
    it moved over the quarters actually available (this project's other
    quarterly fundamentals, already collected — no new fetch needed).

    Revenue can grow while a company quietly loads up on debt (bond
    issuance funding growth rather than operations), so this is a separate
    check from classify_revenue_consistency, not a substitute for it.
    """
    h = ticker_history.dropna(subset=["debt_to_equity"]).sort_values("period")
    if h.empty:
        return False, "부채비율 데이터 부족"

    latest = h["debt_to_equity"].iloc[-1]
    earliest = h["debt_to_equity"].iloc[0]

    if latest > max_debt_to_equity:
        return False, f"부채비율 높음 (현재 {latest:.2f})"

    if earliest > 0 and (latest / earliest - 1) > max_increase:
        return False, f"최근 분기 사이 부채비율 급증 — 회사채 발행 등 가능성 ({earliest:.2f} → {latest:.2f})"

    return True, f"부채비율 안정적 (현재 {latest:.2f})"


def classify_expense_efficiency(
    ticker_history: pd.DataFrame, min_operating_margin: float = 0.05, max_decline: float = 0.3
) -> tuple[bool, str]:
    """Is this company's cost base under control relative to its own
    revenue — from its own quarterly operating margin (== 1 minus operating
    expenses/revenue), already collected, no new fetch needed.

    Flags a company as expense-heavy either because its operating margin is
    too thin right now, or because it eroded sharply across the quarters
    actually available (costs growing faster than revenue) even if the
    absolute level still clears the bar today.
    """
    h = ticker_history.dropna(subset=["operating_margin"]).sort_values("period")
    if h.empty:
        return False, "영업이익률 데이터 부족"

    latest = h["operating_margin"].iloc[-1]
    earliest = h["operating_margin"].iloc[0]

    if latest < min_operating_margin:
        return False, f"영업이익률 낮음 — 매출 대비 비용 과다 (현재 {latest * 100:.1f}%)"

    if earliest > 0 and (latest / earliest - 1) < -max_decline:
        return False, f"영업이익률 급격히 악화 — 비용 증가 속도가 매출보다 빠름 ({earliest * 100:.1f}% → {latest * 100:.1f}%)"

    return True, f"영업이익률 안정적 (현재 {latest * 100:.1f}%)"
