import pandas as pd
import pytest

from stock_valuation.entry_timing import (
    ZONE_FAVORABLE,
    ZONE_NEUTRAL,
    ZONE_STRETCHED,
    ZONE_UNKNOWN,
    classify_entry_zone,
    compute_entry_zone_metrics,
)


def _prices(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-01", periods=len(closes))
    return pd.DataFrame({"date": dates, "close": closes})


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
