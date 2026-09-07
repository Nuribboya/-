import pandas as pd
import pytest

from stock_valuation.entry_timing import (
    RALLY_BUBBLE_LIKE,
    RALLY_FUNDAMENTALS_BACKED,
    RALLY_UNKNOWN,
    ZONE_FAVORABLE,
    ZONE_NEUTRAL,
    ZONE_STRETCHED,
    ZONE_UNKNOWN,
    classify_entry_zone,
    classify_rally_support,
    compute_entry_zone_metrics,
)


def _prices(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-01", periods=len(closes))
    return pd.DataFrame({"date": dates, "close": closes})


def _annual_revenue(revenues: list[float], start_year: str = "2020-12-31") -> pd.DataFrame:
    years = pd.date_range(start_year, periods=len(revenues), freq="YE")
    return pd.DataFrame({"year": years, "revenue": revenues})


def _price_points(start_date: str, start_price: float, end_date: str, end_price: float) -> pd.DataFrame:
    # Only the prices at/around the annual-revenue window's endpoints
    # matter here (classify_rally_support looks them up via .asof()), so a
    # sparse two-point series is enough to exercise the comparison.
    return pd.DataFrame(
        {"date": pd.to_datetime([start_date, end_date]), "close": [start_price, end_price]}
    )


def test_metrics_empty_for_no_price_history():
    assert compute_entry_zone_metrics(pd.DataFrame(columns=["date", "close"])) == {}


def test_metrics_use_trailing_window_and_current_price():
    closes = [100.0] * 300 + [90.0] * 50 + [95.0]  # low of 90 recently, ending at 95
    metrics = compute_entry_zone_metrics(_prices(closes))
    assert metrics["current_price"] == pytest.approx(95.0)
    assert metrics["low_52w"] == pytest.approx(90.0)


def test_metrics_shorter_history_still_works():
    # Only 10 days of history — far short of 50/200/252, should not crash
    # and should just average/window over what's there.
    metrics = compute_entry_zone_metrics(_prices([10.0, 11.0, 9.0, 10.5, 10.0, 9.5, 10.0, 10.2, 9.8, 10.0]))
    assert metrics["current_price"] == pytest.approx(10.0)
    assert metrics["low_52w"] == pytest.approx(9.0)


def test_classify_favorable_when_near_trailing_low():
    metrics = {"current_price": 100.0, "low_52w": 95.0, "ma_200": 110.0, "pct_above_low": 100 / 95 - 1}
    zone, detail = classify_entry_zone(metrics)
    assert zone == ZONE_FAVORABLE
    assert "저점" in detail


def test_classify_favorable_when_below_200day_average_even_if_off_the_low():
    metrics = {"current_price": 100.0, "low_52w": 70.0, "ma_200": 105.0, "pct_above_low": 100 / 70 - 1}
    zone, _ = classify_entry_zone(metrics)
    assert zone == ZONE_FAVORABLE


def test_classify_stretched_when_far_above_trailing_low():
    metrics = {"current_price": 140.0, "low_52w": 100.0, "ma_200": 110.0, "pct_above_low": 140 / 100 - 1}
    zone, _ = classify_entry_zone(metrics)
    assert zone == ZONE_STRETCHED


def test_classify_neutral_in_between():
    metrics = {"current_price": 115.0, "low_52w": 100.0, "ma_200": 110.0, "pct_above_low": 115 / 100 - 1}
    zone, _ = classify_entry_zone(metrics)
    assert zone == ZONE_NEUTRAL


def test_classify_unknown_for_empty_metrics():
    zone, detail = classify_entry_zone({})
    assert zone == ZONE_UNKNOWN
    assert "부족" in detail


def test_rally_flagged_as_bubble_when_price_far_outruns_revenue():
    # Revenue up 30% over the window, price up 300% — price grew ~10x
    # faster than the business did.
    revenue = _annual_revenue([100, 110, 121, 130])
    prices = _price_points("2020-12-31", 50.0, "2023-12-31", 200.0)
    label, detail = classify_rally_support(prices, revenue)
    assert label == RALLY_BUBBLE_LIKE
    assert "매출" in detail


def test_rally_backed_by_fundamentals_when_price_tracks_revenue():
    # Revenue up 30%, price up 25% — roughly in line with the business.
    revenue = _annual_revenue([100, 110, 121, 130])
    prices = _price_points("2020-12-31", 100.0, "2023-12-31", 125.0)
    label, _ = classify_rally_support(prices, revenue)
    assert label == RALLY_FUNDAMENTALS_BACKED


def test_rally_backed_when_price_did_not_rise():
    revenue = _annual_revenue([100, 110, 121, 130])
    prices = _price_points("2020-12-31", 100.0, "2023-12-31", 90.0)
    label, detail = classify_rally_support(prices, revenue)
    assert label == RALLY_FUNDAMENTALS_BACKED
    assert "오르지 않음" in detail


def test_rally_flagged_as_bubble_when_revenue_flat_but_price_rose():
    revenue = _annual_revenue([100, 99, 101, 100])
    prices = _price_points("2020-12-31", 50.0, "2023-12-31", 150.0)
    label, detail = classify_rally_support(prices, revenue)
    assert label == RALLY_BUBBLE_LIKE
    assert "매출은 늘지 않았는데" in detail


def test_rally_unknown_with_too_few_years_of_revenue():
    revenue = _annual_revenue([100, 110])
    prices = _price_points("2020-12-31", 50.0, "2021-12-31", 60.0)
    label, reason = classify_rally_support(prices, revenue)
    assert label == RALLY_UNKNOWN
    assert "부족" in reason


def test_rally_unknown_when_price_history_missing_the_window():
    revenue = _annual_revenue([100, 110, 121, 130])
    prices = pd.DataFrame(columns=["date", "close"])
    label, reason = classify_rally_support(prices, revenue)
    assert label == RALLY_UNKNOWN
    assert "부족" in reason
