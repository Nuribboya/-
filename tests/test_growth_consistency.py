import pandas as pd
import pytest

from stock_valuation.growth_consistency import classify_revenue_consistency


def _annual(revenues):
    years = pd.date_range("2020-12-31", periods=len(revenues), freq="YE")
    return pd.DataFrame({"year": years, "revenue": revenues})


def test_steady_growth_across_all_available_years_passes():
    ok, reason = classify_revenue_consistency(_annual([100, 110, 121, 133]))
    assert ok is True
    assert "꾸준히 증가" in reason


def test_a_real_yoy_decline_fails():
    ok, reason = classify_revenue_consistency(_annual([100, 110, 90, 133]))
    assert ok is False
    assert "감소" in reason


def test_trivial_dip_within_tolerance_still_passes():
    # -1% year-over-year is noise, not a real decline, given the 2% default tolerance.
    ok, _ = classify_revenue_consistency(_annual([100, 110, 108.9, 120]))
    assert ok is True


def test_too_few_years_reports_insufficient_data():
    ok, reason = classify_revenue_consistency(_annual([100, 110]), min_years=3)
    assert ok is False
    assert "데이터 부족" in reason


def test_flat_or_declining_total_growth_fails_even_without_yoy_dips():
    # No single-year dip, but the multi-year trend is still non-positive overall.
    ok, _ = classify_revenue_consistency(_annual([100, 100, 100, 100]))
    assert ok is False


def _quarters(debt_to_equity):
    periods = pd.date_range("2023-03-31", periods=len(debt_to_equity), freq="QE")
    return pd.DataFrame({"period": periods, "debt_to_equity": debt_to_equity})


def test_stable_low_debt_passes():
    from stock_valuation.growth_consistency import classify_debt_health

    ok, reason = classify_debt_health(_quarters([0.5, 0.52, 0.5, 0.51]))
    assert ok is True
    assert "안정적" in reason


def test_high_absolute_debt_fails():
    from stock_valuation.growth_consistency import classify_debt_health

    ok, reason = classify_debt_health(_quarters([2.5, 2.6, 2.5, 2.7]), max_debt_to_equity=2.0)
    assert ok is False
    assert "높음" in reason


def test_sharp_debt_increase_fails_even_if_absolute_level_is_low():
    from stock_valuation.growth_consistency import classify_debt_health

    ok, reason = classify_debt_health(_quarters([0.3, 0.4, 0.5, 0.6]), max_debt_to_equity=2.0, max_increase=0.5)
    assert ok is False
    assert "급증" in reason


def test_missing_debt_data_fails_with_clear_reason():
    from stock_valuation.growth_consistency import classify_debt_health

    ok, reason = classify_debt_health(_quarters([None, None]))
    assert ok is False
    assert "부족" in reason


def _margin_quarters(operating_margins):
    periods = pd.date_range("2023-03-31", periods=len(operating_margins), freq="QE")
    return pd.DataFrame({"period": periods, "operating_margin": operating_margins})


def test_healthy_operating_margin_passes():
    from stock_valuation.growth_consistency import classify_expense_efficiency

    ok, reason = classify_expense_efficiency(_margin_quarters([0.20, 0.21, 0.19, 0.20]))
    assert ok is True
    assert "안정적" in reason


def test_low_absolute_operating_margin_fails():
    from stock_valuation.growth_consistency import classify_expense_efficiency

    ok, reason = classify_expense_efficiency(_margin_quarters([0.03, 0.02, 0.03, 0.02]), min_operating_margin=0.05)
    assert ok is False
    assert "비용 과다" in reason


def test_sharp_margin_erosion_fails_even_if_still_above_minimum():
    from stock_valuation.growth_consistency import classify_expense_efficiency

    ok, reason = classify_expense_efficiency(
        _margin_quarters([0.30, 0.25, 0.20, 0.15]), min_operating_margin=0.05, max_decline=0.3
    )
    assert ok is False
    assert "악화" in reason


def test_missing_operating_margin_data_fails_with_clear_reason():
    from stock_valuation.growth_consistency import classify_expense_efficiency

    ok, reason = classify_expense_efficiency(_margin_quarters([None, None]))
    assert ok is False
    assert "부족" in reason
