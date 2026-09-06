import numpy as np
import pandas as pd
import pytest

from stock_valuation.explanations import build_reason_column, format_cheapness_reason, format_quality_reason
from stock_valuation.model import explain_quality_scores, train_quality_model


def _toy_dataset():
    periods = pd.to_datetime(["2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31", "2021-03-31"])
    rng = np.random.default_rng(0)
    n_tickers = 6
    rows = []
    for t in range(n_tickers):
        for p in periods:
            rows.append(
                {
                    "period": p,
                    "ticker": f"T{t}",
                    "roe_z": rng.normal(),
                    "debt_to_equity_z": rng.normal(),
                    "label": rng.integers(0, 3),
                }
            )
    return pd.DataFrame(rows)


def test_explain_quality_scores_returns_top_n_signed_contributions():
    df = _toy_dataset()
    feature_cols = ["roe_z", "debt_to_equity_z"]
    model, _ = train_quality_model(df, feature_cols)

    explanations = explain_quality_scores(model, df.head(3), feature_cols, top_n=2)
    assert len(explanations) == 3
    for row_explanation in explanations:
        assert len(row_explanation) == 2
        names = {name for name, _ in row_explanation}
        assert names <= set(feature_cols)
        for _, value in row_explanation:
            assert isinstance(value, float)


def test_format_quality_reason_shows_direction_arrows():
    text = format_quality_reason([("roe_z", 0.5), ("debt_to_equity_z", -0.3)])
    assert "ROE(섹터 대비) ↑" in text
    assert "부채비율(섹터 대비) ↓" in text


def test_format_quality_reason_handles_empty_contributions():
    assert format_quality_reason([]) == "설명 불가"


def test_format_cheapness_reason_reports_available_percentiles():
    row = pd.Series({"pe_ratio_pct": 0.12, "pb_ratio_pct": 0.08})
    text = format_cheapness_reason(row)
    assert "P/E 섹터 내 하위 12%" in text
    assert "P/B 섹터 내 하위 8%" in text


def test_format_cheapness_reason_falls_back_when_no_multiples():
    row = pd.Series({"pe_ratio_pct": None, "pb_ratio_pct": None})
    assert format_cheapness_reason(row) == "밸류에이션 데이터 부족"


def test_build_reason_column_looks_up_by_ticker_not_row_position():
    result = pd.DataFrame(
        {"ticker": ["BBB", "AAA"], "pe_ratio_pct": [0.5, 0.1], "pb_ratio_pct": [0.5, 0.2]}
    )
    contributions_by_ticker = {
        "AAA": [("roe_z", 0.9)],
        "BBB": [("debt_to_equity_z", -0.4)],
    }
    reasons = build_reason_column(contributions_by_ticker, result)
    assert "ROE(섹터 대비) ↑" in reasons.iloc[1]  # AAA is row 1 here
    assert "부채비율(섹터 대비) ↓" in reasons.iloc[0]  # BBB is row 0 here
