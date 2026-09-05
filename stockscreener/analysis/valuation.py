"""절댓값 기반 내재가치/저평가 판단.

애널리스트의 목표주가나 투자의견은 전혀 사용하지 않는다. 오직 그레이엄의
개정 내재가치 공식(1962)과 실제 재무제표에서 계산한 과거 성장률만 사용해
계산한다.
"""
from __future__ import annotations

from typing import Optional

from stockscreener.analysis.graham import graham_number
from stockscreener.models import FinancialTrend, ValuationResult

# V = EPS x (8.5 + 2g) x 4.4 / Y  (그레이엄, 1962년 개정 공식)
DEFAULT_AAA_BOND_YIELD_PCT = 4.4  # 공식이 만들어질 당시 기준 AAA 회사채 수익률
BASE_MULTIPLIER = 8.5  # 무성장 기업의 기준 배수
GROWTH_MULTIPLIER = 2.0
NORMALIZING_FACTOR = 4.4
MIN_GROWTH_PCT = 0.0
MAX_GROWTH_PCT = 15.0  # 공식은 완만하게 성장하는 우량주 대상이므로 성장률을 제한한다
DEFAULT_MIN_MARGIN_OF_SAFETY = 0.25  # 그레이엄이 강조한 최소 안전마진(25%)


def _implied_growth_rate_pct(trend: Optional[FinancialTrend]) -> Optional[float]:
    """과거 실적(순이익 또는 매출 CAGR)만으로 성장률을 추정한다. 예측/전망 없음."""
    if trend is None or trend.insufficient_data:
        return None
    candidates = [c for c in (trend.net_income_cagr, trend.revenue_cagr) if c is not None]
    if not candidates:
        return None
    g = candidates[0] * 100
    return max(MIN_GROWTH_PCT, min(MAX_GROWTH_PCT, g))


def intrinsic_value_graham(
    eps: Optional[float],
    growth_rate_pct: Optional[float],
    aaa_bond_yield_pct: float = DEFAULT_AAA_BOND_YIELD_PCT,
) -> Optional[float]:
    """그레이엄의 개정 내재가치 공식: EPS x (8.5 + 2g) x 4.4 / Y."""
    if eps is None or eps <= 0:
        return None
    if aaa_bond_yield_pct is None or aaa_bond_yield_pct <= 0:
        return None
    g = growth_rate_pct if growth_rate_pct is not None else 0.0
    return eps * (BASE_MULTIPLIER + GROWTH_MULTIPLIER * g) * NORMALIZING_FACTOR / aaa_bond_yield_pct


def margin_of_safety(intrinsic_value: Optional[float], price: Optional[float]) -> Optional[float]:
    """(내재가치 - 주가) / 내재가치. 양수일수록 저평가."""
    if intrinsic_value is None or intrinsic_value <= 0 or price is None:
        return None
    return (intrinsic_value - price) / intrinsic_value


def evaluate_valuation(
    ticker: str,
    price: Optional[float],
    eps: Optional[float],
    book_value_per_share: Optional[float],
    trend: Optional[FinancialTrend],
    aaa_bond_yield_pct: float = DEFAULT_AAA_BOND_YIELD_PCT,
    min_margin_of_safety: float = DEFAULT_MIN_MARGIN_OF_SAFETY,
) -> ValuationResult:
    growth_pct = _implied_growth_rate_pct(trend)
    intrinsic = intrinsic_value_graham(eps, growth_pct, aaa_bond_yield_pct)
    gn = graham_number(eps, book_value_per_share)
    mos = margin_of_safety(intrinsic, price)

    price_below_gn = None
    if gn is not None and price is not None:
        price_below_gn = price < gn

    # 두 절댓값 지표(내재가치 안전마진, 그레이엄 넘버) 모두 저평가를 가리킬 때만
    # 저평가로 판정한다 — 한쪽 데이터만으로 성급히 결론 내리지 않는다.
    is_undervalued = bool(
        mos is not None and mos >= min_margin_of_safety and price_below_gn is True
    )

    return ValuationResult(
        ticker=ticker,
        price=price,
        eps_used=eps,
        growth_rate_pct_used=growth_pct,
        intrinsic_value=intrinsic,
        graham_number=gn,
        margin_of_safety=mos,
        price_below_graham_number=price_below_gn,
        is_undervalued=is_undervalued,
    )
