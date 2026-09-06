import pandas as pd
import pytest

from stock_valuation.portfolio import build_portfolio


def _result(tickers, sectors, buy_tiers, quality_scores):
    return pd.DataFrame(
        {"ticker": tickers, "sector": sectors, "buy_tier": buy_tiers, "quality_score": quality_scores}
    )


def test_excludes_no_signal_names_and_weights_sum_to_one():
    result = _result(
        ["AAA", "BBB", "CCC"],
        ["Tech", "Tech", "Health Care"],
        ["3차 매수 (강한 저평가)", "관망", "1차 매수"],
        [0.9, 0.9, 0.5],
    )
    portfolio = build_portfolio(result)
    assert set(portfolio["ticker"]) == {"AAA", "CCC"}  # BBB excluded (관망)
    assert portfolio["weight"].sum() == pytest.approx(1.0)


def test_returns_empty_frame_when_nothing_cleared_a_signal():
    result = _result(["AAA"], ["Tech"], ["관망"], [0.9])
    portfolio = build_portfolio(result)
    assert portfolio.empty
    assert list(portfolio.columns) == ["ticker", "sector", "buy_tier", "quality_score", "weight"]


def test_per_stock_cap_is_respected_and_excess_redistributed():
    # One dominant name would otherwise take ~90% of the raw quality-weighted total.
    result = _result(
        ["AAA", "BBB", "CCC", "DDD"],
        ["Tech", "Health Care", "Financials", "Energy"],
        ["3차 매수 (강한 저평가)"] * 4,
        [0.90, 0.03, 0.03, 0.04],
    )
    portfolio = build_portfolio(result, max_weight_per_stock=0.40)
    assert (portfolio["weight"] <= 0.40 + 1e-9).all()
    assert portfolio["weight"].sum() == pytest.approx(1.0)


def test_per_sector_cap_is_respected_across_multiple_names():
    # Three Tech names would otherwise take most of the portfolio. Three
    # sectors total, so a 0.40 cap is actually satisfiable (3 * 0.40 >= 1.0)
    # — a 2-sector version would make the cap mathematically impossible to
    # honor at all (2 * 0.40 < 1.0), same trap as the per-stock cap above.
    result = _result(
        ["AAA", "BBB", "CCC", "DDD", "EEE"],
        ["Tech", "Tech", "Tech", "Energy", "Financials"],
        ["3차 매수 (강한 저평가)"] * 5,
        [0.5, 0.3, 0.2, 0.3, 0.3],
    )
    portfolio = build_portfolio(result, max_weight_per_stock=1.0, max_weight_per_sector=0.40)
    sector_totals = portfolio.groupby("sector")["weight"].sum()
    assert (sector_totals <= 0.40 + 1e-6).all()
    assert portfolio["weight"].sum() == pytest.approx(1.0)


def test_prioritizes_stronger_buy_tiers_when_trimming_to_max_positions():
    result = _result(
        ["AAA", "BBB", "CCC"],
        ["Tech", "Health Care", "Financials"],
        ["1차 매수", "3차 매수 (강한 저평가)", "2차 매수"],
        [0.5, 0.5, 0.5],
    )
    portfolio = build_portfolio(result, max_positions=2)
    assert set(portfolio["ticker"]) == {"BBB", "CCC"}  # AAA (weakest tier) trimmed first
