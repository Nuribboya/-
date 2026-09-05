"""5~10개년 재무제표 추세 분석.

애널리스트 의견이 아니라 실제 매출/순이익/주당순이익(EPS) 수치의 변화만으로
성장성과 안정성을 판단한다.
"""
from __future__ import annotations

from stockscreener.models import FinancialTrend, YearlyFinancials

MIN_YEARS_FOR_TREND = 5


def _cagr(first: float | None, last: float | None, periods: int) -> float | None:
    if first is None or last is None or periods <= 0:
        return None
    if first <= 0 or last <= 0:
        # 흑자→적자 전환 등 부호가 바뀌면 CAGR 자체가 무의미하므로 계산하지 않는다.
        return None
    return (last / first) ** (1 / periods) - 1


def _avg(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def analyze_financial_trend(years: list[YearlyFinancials]) -> FinancialTrend:
    """years는 오래된 연도 -> 최신 연도 순으로 정렬되어 있어야 한다."""
    n = len(years)
    if n == 0:
        return FinancialTrend(
            years_available=0,
            fiscal_year_range=None,
            revenue_cagr=None,
            net_income_cagr=None,
            eps_growth_pct=None,
            loss_years=0,
            is_stable=False,
            insufficient_data=True,
        )

    fiscal_year_range = (years[0].fiscal_year, years[-1].fiscal_year)
    loss_years = sum(1 for y in years if y.net_income is not None and y.net_income < 0)
    latest_fcf = years[-1].free_cash_flow

    if n < MIN_YEARS_FOR_TREND:
        return FinancialTrend(
            years_available=n,
            fiscal_year_range=fiscal_year_range,
            revenue_cagr=None,
            net_income_cagr=None,
            eps_growth_pct=None,
            loss_years=loss_years,
            is_stable=False,
            insufficient_data=True,
            latest_free_cash_flow=latest_fcf,
            fcf_cagr=None,
        )

    periods = n - 1
    revenue_cagr = _cagr(years[0].revenue, years[-1].revenue, periods)
    net_income_cagr = _cagr(years[0].net_income, years[-1].net_income, periods)
    fcf_cagr = _cagr(years[0].free_cash_flow, years[-1].free_cash_flow, periods)

    # 그레이엄 방식: 시작/종료 시점 각각 최대 3개년 평균으로 변동성을 완화한다.
    window = min(3, n)
    start_eps = _avg([y.eps for y in years[:window]])
    end_eps = _avg([y.eps for y in years[-window:]])
    eps_growth_pct = None
    if start_eps is not None and end_eps is not None and start_eps != 0:
        eps_growth_pct = (end_eps - start_eps) / abs(start_eps) * 100

    is_stable = loss_years == 0 and eps_growth_pct is not None and eps_growth_pct > 0

    return FinancialTrend(
        years_available=n,
        fiscal_year_range=fiscal_year_range,
        revenue_cagr=revenue_cagr,
        net_income_cagr=net_income_cagr,
        eps_growth_pct=eps_growth_pct,
        loss_years=loss_years,
        is_stable=is_stable,
        insufficient_data=False,
        latest_free_cash_flow=latest_fcf,
        fcf_cagr=fcf_cagr,
    )
