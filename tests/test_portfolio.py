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


def test_tiers_filter_restricts_to_only_the_given_buy_tiers():
    result = _result(
        ["AAA", "BBB", "CCC"],
        ["Tech", "Health Care", "Financials"],
        ["3차 매수 (강한 저평가)", "1차 매수", "2차 매수"],
        [0.5, 0.5, 0.5],
    )
    portfolio = build_portfolio(result, tiers=["3차 매수 (강한 저평가)"])
    assert set(portfolio["ticker"]) == {"AAA"}


def test_causes_filter_keeps_only_matching_undervaluation_cause():
    result = _result(
        ["AAA", "BBB"],
        ["Tech", "Health Care"],
        ["3차 매수 (강한 저평가)", "3차 매수 (강한 저평가)"],
        [0.5, 0.5],
    )
    result["undervaluation_cause"] = ["펀더멘털은 안정적 — 시장이 과매도했을 가능성", "단순 성장 둔화로 보임 (매출/마진 일부 둔화, 다른 지표는 안정적)"]
    portfolio = build_portfolio(result, causes=["펀더멘털은 안정적 — 시장이 과매도했을 가능성"])
    assert set(portfolio["ticker"]) == {"AAA"}


def test_min_avg_volume_filter_drops_illiquid_names():
    result = _result(
        ["AAA", "BBB"],
        ["Tech", "Health Care"],
        ["3차 매수 (강한 저평가)", "3차 매수 (강한 저평가)"],
        [0.5, 0.5],
    )
    result["avg_volume"] = [2_000_000, 5_000]
    portfolio = build_portfolio(result, min_avg_volume=100_000)
    assert set(portfolio["ticker"]) == {"AAA"}


def _steady_result(tickers, sectors, market_caps, consistency_ok, avg_volume=None, debt_ok=None):
    df = pd.DataFrame(
        {
            "ticker": tickers,
            "sector": sectors,
            "market_cap": market_caps,
            "revenue_consistency_ok": consistency_ok,
            "revenue_consistency_reason": ["ok"] * len(tickers),
            "debt_health_ok": debt_ok if debt_ok is not None else [True] * len(tickers),
            "debt_health_reason": ["ok"] * len(tickers),
        }
    )
    if avg_volume is not None:
        df["avg_volume"] = avg_volume
    return df


def test_steady_growth_portfolio_excludes_inconsistent_revenue_names():
    from stock_valuation.portfolio import build_steady_growth_portfolio

    result = _steady_result(
        ["AAA", "BBB"], ["Tech", "Health Care"], [2_000_000_000_000, 1_800_000_000_000], [True, False]
    )
    portfolio = build_steady_growth_portfolio(result)
    assert set(portfolio["ticker"]) == {"AAA"}


def test_steady_growth_portfolio_ranks_by_market_cap_when_trimming():
    from stock_valuation.portfolio import build_steady_growth_portfolio

    result = _steady_result(
        ["AAA", "BBB", "CCC"],
        ["Tech", "Health Care", "Financials"],
        [1_000_000_000_000, 3_000_000_000_000, 2_000_000_000_000],
        [True, True, True],
    )
    portfolio = build_steady_growth_portfolio(result, max_positions=2)
    assert set(portfolio["ticker"]) == {"BBB", "CCC"}  # AAA (smallest market cap) trimmed


def test_steady_growth_portfolio_weight_sums_to_one_and_respects_caps():
    from stock_valuation.portfolio import build_steady_growth_portfolio

    result = _steady_result(
        ["AAA", "BBB", "CCC", "DDD", "EEE"],
        ["Tech", "Tech", "Tech", "Energy", "Financials"],
        [5_000_000_000_000, 1_000_000_000_000, 500_000_000_000, 800_000_000_000, 800_000_000_000],
        [True, True, True, True, True],
    )
    portfolio = build_steady_growth_portfolio(result, max_weight_per_stock=0.40, max_weight_per_sector=0.40)
    assert portfolio["weight"].sum() == pytest.approx(1.0)
    assert (portfolio["weight"] <= 0.40 + 1e-6).all()
    assert (portfolio.groupby("sector")["weight"].sum() <= 0.40 + 1e-6).all()


def test_steady_growth_portfolio_min_avg_volume_filter():
    from stock_valuation.portfolio import build_steady_growth_portfolio

    result = _steady_result(
        ["AAA", "BBB"],
        ["Tech", "Health Care"],
        [1_000_000_000_000, 1_000_000_000_000],
        [True, True],
        avg_volume=[2_000_000, 5_000],
    )
    portfolio = build_steady_growth_portfolio(result, min_avg_volume=100_000)
    assert set(portfolio["ticker"]) == {"AAA"}


def test_steady_growth_portfolio_empty_when_nothing_qualifies():
    from stock_valuation.portfolio import build_steady_growth_portfolio

    result = _steady_result(["AAA"], ["Tech"], [1_000_000_000_000], [False])
    portfolio = build_steady_growth_portfolio(result)
    assert portfolio.empty
    assert list(portfolio.columns) == [
        "ticker",
        "sector",
        "market_cap",
        "revenue_consistency_reason",
        "debt_health_reason",
        "weight",
    ]


def test_steady_growth_portfolio_excludes_poor_debt_health_by_default():
    from stock_valuation.portfolio import build_steady_growth_portfolio

    result = _steady_result(
        ["AAA", "BBB"],
        ["Tech", "Health Care"],
        [2_000_000_000_000, 1_800_000_000_000],
        [True, True],
        debt_ok=[True, False],
    )
    portfolio = build_steady_growth_portfolio(result)
    assert set(portfolio["ticker"]) == {"AAA"}


def test_steady_growth_portfolio_can_skip_debt_health_filter():
    from stock_valuation.portfolio import build_steady_growth_portfolio

    result = _steady_result(
        ["AAA", "BBB"],
        ["Tech", "Health Care"],
        [2_000_000_000_000, 1_800_000_000_000],
        [True, True],
        debt_ok=[True, False],
    )
    portfolio = build_steady_growth_portfolio(result, require_debt_health=False)
    assert set(portfolio["ticker"]) == {"AAA", "BBB"}


def test_steady_growth_portfolio_tolerates_missing_debt_health_column_when_disabled():
    from stock_valuation.portfolio import build_steady_growth_portfolio

    result = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "sector": ["Tech"],
            "market_cap": [1_000_000_000_000],
            "revenue_consistency_ok": [True],
            "revenue_consistency_reason": ["ok"],
        }
    )
    portfolio = build_steady_growth_portfolio(result, require_debt_health=False)
    assert set(portfolio["ticker"]) == {"AAA"}
    assert "debt_health_reason" in portfolio.columns
