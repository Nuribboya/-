import math

from stockscreener.analysis.valuation import (
    evaluate_valuation,
    intrinsic_value_graham,
    margin_of_safety,
)
from stockscreener.models import FinancialTrend


def test_intrinsic_value_zero_growth_matches_base_multiplier():
    # Y = 4.4(기본값) 일 때 정규화 항이 1이 되어 EPS * 8.5 로 단순화된다
    value = intrinsic_value_graham(eps=2.0, growth_rate_pct=0.0, aaa_bond_yield_pct=4.4)
    assert math.isclose(value, 2.0 * 8.5, rel_tol=1e-9)


def test_intrinsic_value_none_for_nonpositive_eps():
    assert intrinsic_value_graham(eps=0.0, growth_rate_pct=5.0) is None
    assert intrinsic_value_graham(eps=-1.0, growth_rate_pct=5.0) is None
    assert intrinsic_value_graham(eps=None, growth_rate_pct=5.0) is None


def test_intrinsic_value_scales_with_growth():
    low = intrinsic_value_graham(eps=2.0, growth_rate_pct=0.0)
    high = intrinsic_value_graham(eps=2.0, growth_rate_pct=10.0)
    assert high > low


def test_margin_of_safety_basic():
    assert math.isclose(margin_of_safety(100.0, 75.0), 0.25)
    assert margin_of_safety(None, 75.0) is None
    assert margin_of_safety(0.0, 75.0) is None


def _trend(net_income_cagr=None, revenue_cagr=None):
    return FinancialTrend(
        years_available=6,
        fiscal_year_range=(2015, 2020),
        revenue_cagr=revenue_cagr,
        net_income_cagr=net_income_cagr,
        eps_growth_pct=40.0,
        loss_years=0,
        is_stable=True,
        insufficient_data=False,
    )


def test_evaluate_valuation_undervalued_requires_all_signals():
    trend = _trend(net_income_cagr=0.05)
    # 주가가 매우 낮아 안전마진/그레이엄 넘버를 충족하고, 부채비율도 낮은 경우
    result = evaluate_valuation(
        ticker="TEST",
        price=5.0,
        eps=2.0,
        book_value_per_share=10.0,
        trend=trend,
        debt_to_equity=0.5,
    )
    assert result.graham_number is not None
    assert result.price_below_graham_number is True
    assert result.margin_of_safety is not None
    assert result.debt_to_equity_ok is True
    assert result.is_undervalued is True


def test_evaluate_valuation_not_undervalued_when_price_high():
    trend = _trend(net_income_cagr=0.05)
    result = evaluate_valuation(
        ticker="TEST",
        price=100.0,
        eps=2.0,
        book_value_per_share=10.0,
        trend=trend,
        debt_to_equity=0.5,
    )
    assert result.is_undervalued is False


def test_evaluate_valuation_blocked_when_debt_to_equity_too_high():
    trend = _trend(net_income_cagr=0.05)
    # 안전마진/그레이엄 넘버는 통과하지만 부채비율이 기준(기본 1.0)을 초과
    result = evaluate_valuation(
        ticker="TEST",
        price=5.0,
        eps=2.0,
        book_value_per_share=10.0,
        trend=trend,
        debt_to_equity=2.5,
    )
    assert result.debt_to_equity_ok is False
    assert result.is_undervalued is False


def test_evaluate_valuation_blocked_when_debt_to_equity_unknown():
    trend = _trend(net_income_cagr=0.05)
    # 부채비율을 확인할 수 없으면(None) 성급히 저평가로 판정하지 않는다
    result = evaluate_valuation(
        ticker="TEST",
        price=5.0,
        eps=2.0,
        book_value_per_share=10.0,
        trend=trend,
        debt_to_equity=None,
    )
    assert result.debt_to_equity_ok is None
    assert result.is_undervalued is False


def test_evaluate_valuation_custom_max_debt_to_equity_threshold():
    trend = _trend(net_income_cagr=0.05)
    result = evaluate_valuation(
        ticker="TEST",
        price=5.0,
        eps=2.0,
        book_value_per_share=10.0,
        trend=trend,
        debt_to_equity=0.4,
        max_debt_to_equity=0.3,
    )
    assert result.debt_to_equity_ok is False
    assert result.is_undervalued is False


def test_evaluate_valuation_missing_data_does_not_crash():
    result = evaluate_valuation(
        ticker="TEST",
        price=None,
        eps=None,
        book_value_per_share=None,
        trend=None,
    )
    assert result.is_undervalued is False
    assert result.intrinsic_value is None
    assert result.graham_number is None
