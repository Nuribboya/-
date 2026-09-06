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
    parser.add_argument(
        "--with-rl",
        action="store_true",
        help="add a tabular Q-learning staged-buy recommendation (experimental, trained "
        "on in-sample historical data — see README caveats before trusting its output)",
    )
    parser.add_argument(
        "--notify-topic",
        type=str,
        default=None,
        help="ntfy.sh topic to push a phone notification to when a 3차 매수(강한 저평가) "
        "signal shows up in this run (see README for setup — free, no account needed)",
    )
    parser.add_argument(
        "--portfolio",
        action="store_true",
        help="build a weighted candidate portfolio from this run's buy signals "
        "(diversification rules, not a real optimizer — see README)",
    )
    parser.add_argument("--portfolio-output", type=str, default="portfolio.csv")
    parser.add_argument("--portfolio-max-positions", type=int, default=15)
    parser.add_argument("--portfolio-max-weight-per-stock", type=float, default=0.15)
    parser.add_argument("--portfolio-max-weight-per-sector", type=float, default=0.30)
    args = parser.parse_args()

    result, metrics = run_pipeline(
        tickers_limit=args.limit,
        start=args.start,
        horizon_days=args.horizon_days,
        use_filing_text=args.with_text,
        text_components=args.text_components,
        use_rl=args.with_rl,
    )
    result.to_csv(args.output, index=False)

    print(f"train/test rows: {metrics.get('n_train')}/{metrics.get('n_test')}")
    if "accuracy" in metrics:
        print(f"holdout accuracy: {metrics['accuracy']:.3f}")

    pd.set_option("display.max_colwidth", None)
    pd.set_option("display.width", 200)
    columns = [
        "ticker",
        "sector",
        "quality_score",
        "cheapness_percentile",
        "buy_tier",
        "reason",
        "undervaluation_cause",
    ]
    if args.with_rl:
        columns.append("rl_action")
    print(result[columns])
    print(f"\nsaved to {args.output}")

    if args.notify_topic:
        from stock_valuation.notify import format_notification_message, send_ntfy_notification, strong_buy_tickers

        tickers = strong_buy_tickers(result)
        if tickers:
            send_ntfy_notification(args.notify_topic, format_notification_message(tickers))
            print(f"notified topic '{args.notify_topic}': {tickers}")

    if args.portfolio:
        from stock_valuation.portfolio import build_portfolio

        portfolio = build_portfolio(
            result,
            max_positions=args.portfolio_max_positions,
            max_weight_per_stock=args.portfolio_max_weight_per_stock,
            max_weight_per_sector=args.portfolio_max_weight_per_sector,
        )
        portfolio.to_csv(args.portfolio_output, index=False)
        print("\n=== 포트폴리오 (진단/실험용 — 실제 매매 전 반드시 직접 검토하세요) ===")
        print(portfolio)
        print(f"saved to {args.portfolio_output}")


if __name__ == "__main__":
    main()
