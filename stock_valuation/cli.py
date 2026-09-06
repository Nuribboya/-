from __future__ import annotations

import argparse

import pandas as pd

from stock_valuation.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Fundamentals-based long-term valuation screener")
    parser.add_argument("--limit", type=int, default=30, help="number of S&P500 tickers to use")
    parser.add_argument("--start", type=str, default="2015-01-01", help="history start date")
    parser.add_argument("--horizon-days", type=int, default=252, help="forward return horizon")
    parser.add_argument("--output", type=str, default="valuation_signal.csv")
    parser.add_argument(
        "--with-text",
        action="store_true",
        help="add 10-K/10-Q filing-text embeddings (needs `pip install -r stock_valuation/requirements-text.txt`)",
    )
    parser.add_argument(
        "--text-components",
        type=int,
        default=4,
        help="PCA dimensions for filing-text embeddings (keep small relative to training rows — "
        "too many relative to sample size lets the model overfit to text noise; raise this only "
        "once you're running with a large --limit)",
    )
    args = parser.parse_args()

    result, metrics = run_pipeline(
        tickers_limit=args.limit,
        start=args.start,
        horizon_days=args.horizon_days,
        use_filing_text=args.with_text,
        text_components=args.text_components,
    )
    result.to_csv(args.output, index=False)

    print(f"train/test rows: {metrics.get('n_train')}/{metrics.get('n_test')}")
    if "accuracy" in metrics:
        print(f"holdout accuracy: {metrics['accuracy']:.3f}")

    pd.set_option("display.max_colwidth", None)
    pd.set_option("display.width", 200)
    print(result[["ticker", "sector", "quality_score", "cheapness_percentile", "buy_tier", "reason"]])
    print(f"\nsaved to {args.output}")


if __name__ == "__main__":
    main()
