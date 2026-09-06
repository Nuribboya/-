import pandas as pd
import pytest

from stock_valuation.features import add_growth, add_ratios, add_sector_relative_zscores, build_feature_table
from stock_valuation.labels import add_relative_return_tiers, compute_forward_returns
from stock_valuation.valuation import add_cheapness_percentile, assign_buy_tier, build_valuation_signal, compute_valuation_multiples


def _fundamentals():
    periods = pd.date_range("2022-03-31", periods=6, freq="QE")
    rows = []
    for ticker, base_rev, base_ni in [("AAA", 100, 10), ("BBB", 200, 5)]:
        for i, period in enumerate(periods):
            rows.append(
                {
                    "period": period,
                    "ticker": ticker,
                    "revenue": base_rev * (1.05 ** i),
                    "operating_income": base_rev * (1.05 ** i) * 0.2,
                    "net_income": base_ni * (1.05 ** i),
                    "total_debt": base_rev * 0.5,
                    "total_equity": base_rev,
                    "operating_cash_flow": base_ni * (1.05 ** i) * 1.2,
                    "capital_expenditure": -base_ni * 0.3,
                    "shares_outstanding": 1000,
                }
            )
    return pd.DataFrame(rows)


def test_add_ratios_computes_expected_margins():
    df = add_ratios(_fundamentals())
    row = df[(df["ticker"] == "AAA") & (df["period"] == df["period"].min())].iloc[0]
    assert row["operating_margin"] == pytest.approx(0.2)
    assert row["net_margin"] == pytest.approx(10 / 100)


def test_add_growth_is_nan_before_four_quarters_and_positive_after():
    df = add_growth(_fundamentals())
    aaa = df[df["ticker"] == "AAA"].sort_values("period")
    assert aaa["revenue_growth_yoy"].iloc[:4].isna().all()
    assert aaa["revenue_growth_yoy"].iloc[4] == pytest.approx(1.05 ** 4 - 1)


def test_sector_relative_zscore_centers_within_group():
    sector_map = pd.DataFrame({"ticker": ["AAA", "BBB"], "sector": ["Tech", "Tech"]})
    df = add_growth(add_ratios(_fundamentals()))
    df = df.fillna(0)
    zscored = add_sector_relative_zscores(df, sector_map)
    period = zscored["period"].min()
    subset = zscored[zscored["period"] == period]
    assert subset["operating_margin_z"].abs().max() < 1e-6 or subset["operating_margin_z"].isna().all()


def test_build_feature_table_end_to_end_has_no_crash():
    sector_map = pd.DataFrame({"ticker": ["AAA", "BBB"], "sector": ["Tech", "Tech"]})
    macro = pd.DataFrame(
        {"treasury_10y": [4.0] * 6, "cpi": [300.0] * 6, "unemployment_rate": [4.0] * 6, "industrial_production": [100.0] * 6},
        index=pd.date_range("2022-03-31", periods=6, freq="QE"),
    )
    macro.index.name = "period"
    features = build_feature_table(_fundamentals(), sector_map, macro)
    assert "treasury_10y" in features.columns
    assert len(features) == len(_fundamentals())


def _price_series(ticker: str, start_price: float, daily_growth: float, n_days: int = 500):
    dates = pd.bdate_range("2022-01-01", periods=n_days)
    prices = [start_price * (daily_growth ** i) for i in range(n_days)]
    return pd.DataFrame({"date": dates, "ticker": ticker, "close": prices, "volume": 1_000_000})


def test_compute_forward_returns_matches_known_growth():
    price_df = _price_series("AAA", 100.0, 1.001)
    period = [price_df["date"].iloc[10]]
    result = compute_forward_returns(price_df, period, horizon_days=100)
    assert len(result) == 1
    assert result.iloc[0]["forward_return"] > 0


def test_forward_returns_skip_periods_without_enough_future_data():
    price_df = _price_series("AAA", 100.0, 1.001, n_days=50)
    period = [price_df["date"].iloc[40]]
    result = compute_forward_returns(price_df, period, horizon_days=252)
    assert result.empty


def test_relative_return_tiers_rank_within_period():
    forward_returns = pd.DataFrame(
        {
            "period": [pd.Timestamp("2023-01-01")] * 6,
            "ticker": list("ABCDEF"),
            "forward_return": [-0.2, -0.1, 0.0, 0.05, 0.2, 0.3],
        }
    )
    tiered = add_relative_return_tiers(forward_returns, n_tiers=3)
    assert tiered.sort_values("forward_return")["label"].tolist() == [0, 0, 1, 1, 2, 2]


def test_assign_buy_tier_gates_on_quality():
    assert assign_buy_tier(0.05, quality_score=0.9, quality_cutoff=0.5) == "3차 매수 (강한 저평가)"
    assert assign_buy_tier(0.05, quality_score=0.1, quality_cutoff=0.5) == "관망"
    assert assign_buy_tier(0.9, quality_score=0.9, quality_cutoff=0.5) == "관망"


def test_valuation_multiples_and_signal_end_to_end():
    latest_price = pd.DataFrame({"ticker": ["AAA", "BBB"], "close": [50.0, 200.0]})
    eps_book = pd.DataFrame(
        {"ticker": ["AAA", "BBB"], "trailing_eps": [5.0, 2.0], "book_value_per_share": [25.0, 40.0]}
    )
    sector_map = pd.DataFrame({"ticker": ["AAA", "BBB"], "sector": ["Tech", "Tech"]})

    multiples = compute_valuation_multiples(latest_price, eps_book, sector_map)
    assert multiples.loc[multiples["ticker"] == "AAA", "pe_ratio"].iloc[0] == pytest.approx(10.0)

    ranked = add_cheapness_percentile(multiples)
    merged = ranked.assign(quality_score=[0.9, 0.9])
    signal = build_valuation_signal(merged)
    assert set(signal["buy_tier"]) <= {
        "3차 매수 (강한 저평가)",
        "2차 매수",
        "1차 매수",
        "관망",
    }
