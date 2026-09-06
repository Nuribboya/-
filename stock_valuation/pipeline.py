from __future__ import annotations

import pandas as pd

from stock_valuation.config import get_sp500_universe
from stock_valuation.data.fundamentals import fetch_quarterly_fundamentals
from stock_valuation.data.macro import fetch_macro_indicators
from stock_valuation.data.prices import fetch_price_history, fetch_trailing_eps_and_book
from stock_valuation.features import build_feature_table, feature_columns
from stock_valuation.labels import add_relative_return_tiers, compute_forward_returns
from stock_valuation.model import drop_dead_feature_columns, predict_quality_score, train_quality_model
from stock_valuation.valuation import add_cheapness_percentile, build_valuation_signal, compute_valuation_multiples


def _fetch_all_fundamentals(tickers: list[str]) -> pd.DataFrame:
    frames = []
    for ticker in tickers:
        try:
            frames.append(fetch_quarterly_fundamentals(ticker))
        except Exception:
            continue
    return pd.concat(frames).reset_index() if frames else pd.DataFrame()


def _fetch_all_prices(tickers: list[str], start: str) -> pd.DataFrame:
    frames = []
    for ticker in tickers:
        try:
            frames.append(fetch_price_history(ticker, start=start))
        except Exception:
            continue
    return pd.concat(frames).reset_index(drop=True) if frames else pd.DataFrame()


def run_pipeline(
    tickers_limit: int | None = 30,
    start: str = "2015-01-01",
    horizon_days: int = 252,
) -> tuple[pd.DataFrame, dict]:
    universe = get_sp500_universe(limit=tickers_limit)
    tickers = universe["ticker"].tolist()

    fundamentals = _fetch_all_fundamentals(tickers)
    # Normalize once, up front: fundamentals/macro/price sources can each
    # hand back a different datetime64 resolution, and every downstream
    # merge (features<->macro, features<->labels) needs an exact dtype
    # match on "period" or it silently joins zero rows.
    fundamentals["period"] = pd.to_datetime(fundamentals["period"]).astype("datetime64[ns]")

    macro = fetch_macro_indicators(start=start)
    features = build_feature_table(fundamentals, universe[["ticker", "sector"]], macro)

    prices = _fetch_all_prices(tickers, start=start)
    periods = sorted(fundamentals["period"].unique())
    forward_returns = compute_forward_returns(prices, periods, horizon_days=horizon_days)
    labeled = add_relative_return_tiers(forward_returns)

    dataset = features.merge(labeled[["period", "ticker", "label"]], on=["period", "ticker"], how="inner")
    feat_cols = [c for c in feature_columns() if c in dataset.columns]

    # yfinance's free quarterly statements only go back ~4-5 quarters, so a
    # YoY (4-quarter-lag) growth feature can be entirely NaN for every row
    # depending on how much history came back. Drop whatever has no signal
    # in this run rather than let one dead column wipe out all training rows.
    feat_cols, dead_cols = drop_dead_feature_columns(dataset, feat_cols)
    if dead_cols:
        print(f"[pipeline] dropping fully-null feature columns (insufficient history?): {dead_cols}")

    print(
        f"[pipeline] fundamentals={len(fundamentals)} macro={len(macro)} "
        f"features={len(features)} prices={len(prices)} "
        f"forward_returns={len(forward_returns)} labeled={len(labeled)} "
        f"dataset={len(dataset)} feat_cols={feat_cols}"
    )

    model, metrics = train_quality_model(dataset, feat_cols)

    latest_period = dataset["period"].max()
    latest_features = dataset[dataset["period"] == latest_period].copy()
    latest_features["quality_score"] = predict_quality_score(model, latest_features, feat_cols)

    latest_prices = prices.sort_values("date").groupby("ticker").tail(1)
    eps_book = pd.DataFrame(fetch_trailing_eps_and_book(t) for t in tickers)
    multiples = compute_valuation_multiples(latest_prices, eps_book, universe[["ticker", "sector"]])
    multiples = add_cheapness_percentile(multiples)

    merged = latest_features[["ticker", "quality_score"]].merge(
        multiples[["ticker", "sector", "close", "pe_ratio", "pb_ratio", "cheapness_percentile"]],
        on="ticker",
        how="inner",
    )
    result = build_valuation_signal(merged)
    return result, metrics
