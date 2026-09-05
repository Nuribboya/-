"""CLI 진입점.

사용 예:
    python -m stockscreener.cli --tickers AAPL,MSFT,005930.KS
    python -m stockscreener.cli --tickers-file watchlist.txt --json out.json
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from stockscreener import config
from stockscreener import report as report_fmt
from stockscreener.screener import Screener


def _load_tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        return [t.strip() for t in args.tickers.split(",") if t.strip()]
    if args.tickers_file:
        path = Path(args.tickers_file)
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    return list(config.DEFAULT_TICKERS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="그레이엄 지표 기반 저평가 주식 스크리너 (애널리스트 의견 미사용, 절댓값 기반 분석)"
    )
    parser.add_argument("--tickers", help="쉼표로 구분한 티커 목록 (예: AAPL,MSFT,005930.KS)")
    parser.add_argument("--tickers-file", help="한 줄에 하나씩 티커가 적힌 파일 경로")
    parser.add_argument(
        "--years",
        type=int,
        default=config.MAX_FINANCIAL_YEARS,
        help="분석할 최대 재무제표 연도 수 (기본 10, 데이터 소스가 적게 제공하면 가능한 만큼만 사용)",
    )
    parser.add_argument(
        "--min-margin-of-safety",
        type=float,
        default=config.DEFAULT_MIN_MARGIN_OF_SAFETY,
        help="저평가로 판단할 최소 안전마진 (기본 0.25 = 25%%)",
    )
    parser.add_argument(
        "--bond-yield",
        type=float,
        default=config.DEFAULT_AAA_BOND_YIELD_PCT,
        help="내재가치 계산에 쓸 AAA 회사채 수익률(%%) (기본 4.4)",
    )
    parser.add_argument("--no-news", action="store_true", help="뉴스 조회를 건너뛴다")
    parser.add_argument("--json", help="JSON 결과를 저장할 파일 경로")
    parser.add_argument(
        "--quiet", action="store_true", help="개별 종목 상세 출력을 생략하고 요약만 표시"
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    tickers = _load_tickers(args)
    if not tickers:
        print("분석할 티커가 없습니다.", file=sys.stderr)
        return 1

    screener = Screener(
        max_financial_years=args.years,
        aaa_bond_yield_pct=args.bond_yield,
        min_margin_of_safety=args.min_margin_of_safety,
        fetch_ticker_news=not args.no_news,
    )

    market_news = [] if args.no_news else screener.get_market_news()
    print(report_fmt.format_market_news(market_news))
    print()

    reports = screener.analyze(tickers)

    if not args.quiet:
        for r in reports:
            print(report_fmt.format_stock_report(r))
            print()

    undervalued = screener.undervalued(reports)
    print(report_fmt.format_undervalued_list(undervalued))

    errors = [r for r in reports if not r.ok]
    if errors:
        print(
            f"\n(참고: {len(errors)}개 종목은 데이터 조회 실패로 분석에서 제외되었습니다.)",
            file=sys.stderr,
        )

    if args.json:
        Path(args.json).write_text(report_fmt.to_json(reports, market_news), encoding="utf-8")
        print(f"\nJSON 저장 완료: {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
