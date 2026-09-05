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
from stockscreener.analysis.graham import MAX_DEBT_TO_EQUITY
from stockscreener.screener import Screener
from stockscreener.universe import load_sp500_tickers


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
    if args.sp500:
        return load_sp500_tickers()
    return list(config.DEFAULT_TICKERS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="그레이엄 지표 기반 저평가 주식 스크리너 (애널리스트 의견 미사용, 절댓값 기반 분석)"
    )
    parser.add_argument("--tickers", help="쉼표로 구분한 티커 목록 (예: AAPL,MSFT,005930.KS)")
    parser.add_argument("--tickers-file", help="한 줄에 하나씩 티커가 적힌 파일 경로")
    parser.add_argument(
        "--sp500",
        action="store_true",
        help="레포에 번들된 S&P 500 스냅샷(약 500종목) 전체를 스크리닝한다 (--tickers/--tickers-file보다 우선순위 낮음, 시간이 오래 걸릴 수 있음)",
    )
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
    parser.add_argument(
        "--max-debt-to-equity",
        type=float,
        default=MAX_DEBT_TO_EQUITY,
        help="저평가 판정에 허용할 최대 부채비율(총부채/자기자본) (기본 1.0 = 100%%, 이보다 부채가 많으면 아무리 싸도 저평가로 표시하지 않음)",
    )
    parser.add_argument("--no-news", action="store_true", help="시장 뉴스+종목별 뉴스 조회를 모두 건너뛴다")
    parser.add_argument(
        "--no-ticker-news",
        action="store_true",
        help="종목별 개별 뉴스 조회만 건너뛴다 (시장 전체 뉴스는 유지). 대량 스크리닝(--sp500 등)에서 속도를 위해 권장",
    )
    parser.add_argument("--json", help="JSON 결과를 저장할 파일 경로")
    parser.add_argument(
        "--quiet", action="store_true", help="개별 종목 상세 출력을 생략하고 요약만 표시"
    )
    return parser


def _analyze_with_progress(screener: Screener, tickers: list[str]) -> list:
    """screener.analyze()와 동일하지만, 종목이 많을 때 진행 상황을 stderr에 표시한다.

    quiet 모드에서도 진행 중임을 알 수 있도록 종목 하나가 끝날 때마다 한 줄을
    같은 자리에 덮어써서 보여준다 (표준 출력/JSON 저장 내용에는 영향 없음).
    """
    total = len(tickers)
    show_progress = total > 10 and sys.stderr.isatty()
    reports = []
    for i, ticker in enumerate(tickers, start=1):
        reports.append(screener.analyze_ticker(ticker))
        if show_progress:
            print(f"\r진행 중: {i}/{total} 종목 처리됨 ({i/total*100:.0f}%) — 현재: {ticker:<10}",
                  end="", file=sys.stderr, flush=True)
    if show_progress:
        print(file=sys.stderr)
    return reports


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    tickers = _load_tickers(args)
    if not tickers:
        print("분석할 티커가 없습니다.", file=sys.stderr)
        return 1

    if len(tickers) > 50 and not (args.no_news or args.no_ticker_news):
        print(
            f"참고: {len(tickers)}개 종목을 스크리닝합니다 — 종목별 뉴스까지 조회하면 시간이 "
            "오래 걸릴 수 있습니다. 속도를 원하면 --no-ticker-news 를 함께 사용하세요.",
            file=sys.stderr,
        )

    screener = Screener(
        max_financial_years=args.years,
        aaa_bond_yield_pct=args.bond_yield,
        min_margin_of_safety=args.min_margin_of_safety,
        max_debt_to_equity=args.max_debt_to_equity,
        fetch_ticker_news=not (args.no_news or args.no_ticker_news),
    )

    market_news = [] if args.no_news else screener.get_market_news()
    print(report_fmt.format_market_news(market_news))
    print()

    reports = _analyze_with_progress(screener, tickers)

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
