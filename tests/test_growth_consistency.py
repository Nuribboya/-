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
