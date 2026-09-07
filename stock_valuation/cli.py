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
    parser.add_argument(
        "--portfolio-strong-only",
        action="store_true",
        help="restrict portfolio candidates to the strongest tier only (3차 매수, 강한 저평가)",
    )
    parser.add_argument(
        "--portfolio-oversold-only",
        action="store_true",
        help="restrict portfolio candidates to the '펀더멘털은 안정적 — 시장이 과매도했을 가능성' "
        "cause only, excluding value traps and simple growth deceleration",
    )
    parser.add_argument(
        "--portfolio-min-volume",
        type=float,
        default=None,
        help="drop portfolio candidates below this recent average daily volume (liquidity filter)",
    )
    parser.add_argument(
        "--steady-growth-portfolio",
        action="store_true",
        help="build a portfolio from top-market-cap, revenue-consistent names instead of "
        "buy-tier signals (fetches each ticker's annual financials automatically — slower)",
    )
    parser.add_argument("--steady-growth-output", type=str, default="steady_growth_portfolio.csv")
    parser.add_argument("--steady-growth-max-positions", type=int, default=15)
    parser.add_argument("--steady-growth-max-weight-per-stock", type=float, default=0.15)
    parser.add_argument("--steady-growth-max-weight-per-sector", type=float, default=0.30)
    parser.add_argument(
        "--steady-growth-min-volume",
        type=float,
        default=None,
        help="drop steady-growth candidates below this recent average daily volume (liquidity filter)",
    )
    args = parser.parse_args()

    result, metrics = run_pipeline(
        tickers_limit=args.limit,
        start=args.start,
        horizon_days=args.horizon_days,
        use_filing_text=args.with_text,
        text_components=args.text_components,
        use_rl=args.with_rl,
        use_revenue_consistency=args.steady_growth_portfolio,
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
        "entry_zone",
        "entry_zone_detail",
    ]
    if args.with_rl:
        columns.append("rl_action")
    if args.steady_growth_portfolio:
        columns += [
            "market_cap",
            "revenue_consistency_reason",
            "debt_health_reason",
            "expense_efficiency_reason",
            "rally_support",
            "rally_support_reason",
        ]
    print(result[columns])
    print(f"\nsaved to {args.output}")

    if args.notify_topic:
        from stock_valuation.notify import format_notification_message, send_ntfy_notification, strong_buy_tickers

        tickers = strong_buy_tickers(result)
        if tickers:
            send_ntfy_notification(args.notify_topic, format_notification_message(tickers))
            print(f"notified topic '{args.notify_topic}': {tickers}")

    if args.portfolio:
        from stock_valuation.explanations import CAUSE_LIKELY_OVERSOLD
        from stock_valuation.portfolio import build_portfolio
        from stock_valuation.valuation import BUY_TIERS

        portfolio = build_portfolio(
            result,
            max_positions=args.portfolio_max_positions,
            max_weight_per_stock=args.portfolio_max_weight_per_stock,
            max_weight_per_sector=args.portfolio_max_weight_per_sector,
            tiers=[BUY_TIERS[0][1]] if args.portfolio_strong_only else None,
            causes=[CAUSE_LIKELY_OVERSOLD] if args.portfolio_oversold_only else None,
            min_avg_volume=args.portfolio_min_volume,
        )
        portfolio.to_csv(args.portfolio_output, index=False)
        print("\n=== 포트폴리오 (진단/실험용 — 실제 매매 전 반드시 직접 검토하세요) ===")
        print(portfolio)
        print(f"saved to {args.portfolio_output}")

    if args.steady_growth_portfolio:
        from stock_valuation.portfolio import build_steady_growth_portfolio

        steady_portfolio = build_steady_growth_portfolio(
            result,
            max_positions=args.steady_growth_max_positions,
            max_weight_per_stock=args.steady_growth_max_weight_per_stock,
            max_weight_per_sector=args.steady_growth_max_weight_per_sector,
            min_avg_volume=args.steady_growth_min_volume,
        )
        steady_portfolio.to_csv(args.steady_growth_output, index=False)
        print("\n=== 대형주 + 매출 안정형 포트폴리오 (실제 매매 전 반드시 직접 검토하세요) ===")
        print(steady_portfolio)
        print(f"saved to {args.steady_growth_output}")


if __name__ == "__main__":
    main()
