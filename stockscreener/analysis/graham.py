"""벤저민 그레이엄의 '방어적 투자자' 기준 계산.

`The Intelligent Investor` 에서 제시한 7가지 정량 기준에, 부채비율과
잉여현금흐름(FCF)을 보는 강화 필터 2종을 더해 자동 평가한다. 데이터가
부족해 판단할 수 없는 항목은 실패(False)가 아니라 None(판단 불가)으로
남겨, 정보 부족을 기준 미달로 착각하지 않게 한다.
"""
from __future__ import annotations

import math
from typing import Optional

from stockscreener.analysis.financial_trend import MIN_YEARS_FOR_TREND, analyze_financial_trend
from stockscreener.models import GrahamAnalysis, GrahamCriterion, YearlyFinancials

MIN_CURRENT_RATIO = 2.0
MAX_PE = 15.0
MAX_PE_TIMES_PB = 22.5
MIN_EPS_GROWTH_PCT = 33.0
MAX_DEBT_TO_EQUITY = 1.0  # 강화 필터: 총부채가 자기자본의 100%를 넘지 않아야 한다


def graham_number(eps: Optional[float], book_value_per_share: Optional[float]) -> Optional[float]:
    """sqrt(22.5 * EPS * BVPS) — 그레이엄이 제시한 주당 적정가 상한 추정치."""
    if eps is None or book_value_per_share is None:
        return None
    if eps <= 0 or book_value_per_share <= 0:
        return None
    return math.sqrt(22.5 * eps * book_value_per_share)


def _criterion(key: str, label: str, passed: Optional[bool], detail: str) -> GrahamCriterion:
    return GrahamCriterion(key=key, label=label, passed=passed, detail=detail)


def evaluate_graham_criteria(
    ticker: str,
    years: list[YearlyFinancials],
    price: Optional[float],
) -> GrahamAnalysis:
    """years는 오래된 연도 -> 최신 연도 순으로 정렬되어 있어야 한다."""
    criteria: list[GrahamCriterion] = []
    n = len(years)
    latest = years[-1] if years else None

    if n < MIN_YEARS_FOR_TREND:
        criteria.append(
            _criterion(
                "earnings_stability",
                "이익 안정성 (적자 연도 없음)",
                None,
                f"연간 재무제표 {n}개년 확보 (최소 {MIN_YEARS_FOR_TREND}개년 필요)",
            )
        )
        criteria.append(
            _criterion(
                "dividend_record",
                "연속 배당 기록",
                None,
                f"연간 재무제표 {n}개년 확보 (최소 {MIN_YEARS_FOR_TREND}개년 필요)",
            )
        )
    else:
        loss_years = [
            y.fiscal_year for y in years if y.net_income is not None and y.net_income < 0
        ]
        criteria.append(
            _criterion(
                "earnings_stability",
                "이익 안정성 (적자 연도 없음)",
                len(loss_years) == 0,
                f"최근 {n}개년 중 적자 연도: {loss_years or '없음'}",
            )
        )

        paid_years = sum(1 for y in years if (y.dividend_per_share or 0) > 0)
        criteria.append(
            _criterion(
                "dividend_record",
                "연속 배당 기록",
                paid_years == n,
                f"확보된 {n}개년 중 배당 지급 {paid_years}개년",
            )
        )

    trend = analyze_financial_trend(years) if years else None
    if trend is None or trend.insufficient_data or trend.eps_growth_pct is None:
        criteria.append(
            _criterion(
                "eps_growth",
                f"EPS {MIN_EPS_GROWTH_PCT:.0f}% 이상 성장",
                None,
                "EPS 추세를 계산할 데이터가 부족합니다",
            )
        )
    else:
        criteria.append(
            _criterion(
                "eps_growth",
                f"EPS {MIN_EPS_GROWTH_PCT:.0f}% 이상 성장",
                trend.eps_growth_pct >= MIN_EPS_GROWTH_PCT,
                f"EPS 성장률 {trend.eps_growth_pct:.1f}%",
            )
        )

    current_ratio = latest.current_ratio if latest else None
    if current_ratio is None:
        criteria.append(
            _criterion(
                "current_ratio",
                f"유동비율 {MIN_CURRENT_RATIO:.1f} 이상",
                None,
                "유동자산/유동부채 데이터 부족",
            )
        )
    else:
        criteria.append(
            _criterion(
                "current_ratio",
                f"유동비율 {MIN_CURRENT_RATIO:.1f} 이상",
                current_ratio >= MIN_CURRENT_RATIO,
                f"유동비율 {current_ratio:.2f}",
            )
        )

    working_capital = latest.working_capital if latest else None
    long_term_debt = latest.long_term_debt if latest else None
    if working_capital is None or long_term_debt is None:
        criteria.append(
            _criterion(
                "debt_vs_working_capital",
                "장기부채 ≤ 순운전자본",
                None,
                "장기부채 또는 운전자본 데이터 부족",
            )
        )
    else:
        criteria.append(
            _criterion(
                "debt_vs_working_capital",
                "장기부채 ≤ 순운전자본",
                long_term_debt <= working_capital,
                f"장기부채 {long_term_debt:,.0f} vs 순운전자본 {working_capital:,.0f}",
            )
        )

    recent_eps = [y.eps for y in years[-3:] if y.eps is not None] if years else []
    avg_eps_3y = sum(recent_eps) / len(recent_eps) if recent_eps else None

    if price is None or avg_eps_3y is None or avg_eps_3y <= 0:
        criteria.append(
            _criterion(
                "moderate_pe",
                f"PER {MAX_PE:.0f} 이하 (최근 3개년 평균 EPS 기준)",
                None,
                "주가 또는 최근 EPS 데이터 부족",
            )
        )
    else:
        pe = price / avg_eps_3y
        criteria.append(
            _criterion(
                "moderate_pe",
                f"PER {MAX_PE:.0f} 이하 (최근 3개년 평균 EPS 기준)",
                pe <= MAX_PE,
                f"PER {pe:.1f}",
            )
        )

    bvps = latest.book_value_per_share if latest else None
    if price is None or avg_eps_3y is None or avg_eps_3y <= 0 or bvps is None or bvps <= 0:
        criteria.append(
            _criterion(
                "pe_times_pb",
                f"PER x PBR {MAX_PE_TIMES_PB:.1f} 이하",
                None,
                "주가/EPS/BVPS 데이터 부족",
            )
        )
    else:
        pe = price / avg_eps_3y
        pb = price / bvps
        combined = pe * pb
        criteria.append(
            _criterion(
                "pe_times_pb",
                f"PER x PBR {MAX_PE_TIMES_PB:.1f} 이하",
                combined <= MAX_PE_TIMES_PB,
                f"PER x PBR = {combined:.1f} (PER {pe:.1f}, PBR {pb:.1f})",
            )
        )

    debt_to_equity = latest.debt_to_equity if latest else None
    if debt_to_equity is None:
        criteria.append(
            _criterion(
                "debt_to_equity",
                f"부채비율(총부채/자기자본) {MAX_DEBT_TO_EQUITY*100:.0f}% 이하",
                None,
                "총부채 또는 자기자본 데이터 부족",
            )
        )
    else:
        criteria.append(
            _criterion(
                "debt_to_equity",
                f"부채비율(총부채/자기자본) {MAX_DEBT_TO_EQUITY*100:.0f}% 이하",
                debt_to_equity <= MAX_DEBT_TO_EQUITY,
                f"부채비율 {debt_to_equity*100:.1f}%",
            )
        )

    fcf = latest.free_cash_flow if latest else None
    if fcf is None:
        criteria.append(
            _criterion(
                "free_cash_flow",
                "잉여현금흐름(FCF) 흑자",
                None,
                "잉여현금흐름 데이터 부족",
            )
        )
    else:
        criteria.append(
            _criterion(
                "free_cash_flow",
                "잉여현금흐름(FCF) 흑자",
                fcf > 0,
                f"FCF {fcf:,.0f}",
            )
        )

    gn = graham_number(
        latest.eps if latest else None, latest.book_value_per_share if latest else None
    )

    return GrahamAnalysis(ticker=ticker, graham_number=gn, criteria=tuple(criteria))
