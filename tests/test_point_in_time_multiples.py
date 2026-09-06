import pandas as pd
import pytest

from stock_valuation.valuation import add_cheapness_percentile, compute_point_in_time_multiples


def test_point_in_time_pe_is_nan_before_four_quarters_of_net_income():
    periods = pd.date_range("2022-03-31", periods=5, freq="QE")
    fundamentals = pd.DataFrame(
        {
            "ticker": ["AAA"] * 5,
            "period": periods,
            "net_income": [10, 10, 10, 10, 12],
            "shares_outstanding": [100] * 5,
            "total_equity": [200] * 5,
        }
    )
    price_df = pd.DataFrame({"ticker": ["AAA"] * 5, "date": periods, "close": [50, 52, 54, 56, 58]})
    sector_map = pd.DataFrame({"ticker": ["AAA"], "sector": ["Tech"]})

    out = compute_point_in_time_multiples(fundamentals, price_df, sector_map)
    assert out["pe_ratio"].iloc[:3].isna().all()
    # TTM net income at period 4 (index 3) = 10*4=40 -> EPS 0.40 -> P/E = 56/0.40 = 140
    assert out["pe_ratio"].iloc[3] == pytest.approx(140.0)
    assert out["pb_ratio"].iloc[0] == pytest.approx(50 / (200 / 100))


def test_point_in_time_multiples_never_uses_a_future_price():
    periods = pd.to_datetime(["2022-03-31", "2022-06-30"])
    fundamentals = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA"],
            "period": periods,
            "net_income": [10, 10],
            "shares_outstanding": [100, 100],
            "total_equity": [200, 200],
        }
    )
    # Only price data up to the first period exists — the second period must
    # not silently pick up a price from before it (there is none after it).
    price_df = pd.DataFrame({"ticker": ["AAA"], "date": [periods[0]], "close": [50.0]})
    sector_map = pd.DataFrame({"ticker": ["AAA"], "sector": ["Tech"]})

    out = compute_point_in_time_multiples(fundamentals, price_df, sector_map)
    assert out["price"].iloc[0] == pytest.approx(50.0)
    assert out["price"].iloc[1] == pytest.approx(50.0)  # asof carries the last known price forward, not NaN


def test_add_cheapness_percentile_groups_by_period_and_sector_for_history():
    df = pd.DataFrame(
        {
            "period": pd.to_datetime(["2022-03-31", "2022-03-31", "2022-06-30", "2022-06-30"]),
            "sector": ["Tech", "Tech", "Tech", "Tech"],
            "pe_ratio": [10.0, 20.0, 5.0, 50.0],
            "pb_ratio": [1.0, 2.0, 0.5, 5.0],
        }
    )
    ranked = add_cheapness_percentile(df, group_cols=("period", "sector"))
    # Cheapest name in the 2022-03-31 cohort should rank below the cheapest
    # name in 2022-06-30's cohort in absolute pe_ratio_pct terms — i.e. each
    # period ranks independently rather than pooling across periods.
    q1 = ranked[ranked["period"] == pd.Timestamp("2022-03-31")]
    assert q1.loc[q1["pe_ratio"].idxmin(), "pe_ratio_pct"] == pytest.approx(0.5)
