"""원청 후보 탐색 CLI.

    # 키 없이 동작 확인
    python -m prime_contractor.cli screen --offline

    # 실제 조회
    export G2B_SERVICE_KEY=...   # 공공데이터포털 조달청 낙찰정보서비스
    export DART_API_KEY=...      # opendart.fss.or.kr (업종코드 보강, 선택)
    python -m prime_contractor.cli screen --days 180 --out 원청후보.csv
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from prime_contractor.config import load_config
from prime_contractor.pipeline import run_screen
from prime_contractor.report import render_table, write_csv


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prime_contractor",
        description="자동제어 판넬 제조사를 위한 원청 후보 탐색 (기존 원청과 업종 중복 제외)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("screen", help="원청 후보를 찾아 순위를 매긴다")
    s.add_argument("--offline", action="store_true", help="API 없이 샘플 데이터로 실행")
    s.add_argument("--days", type=int, default=None, help="조회 기간(일), 기본 180")
    s.add_argument("--config", default=None, help="설정 JSON 경로")
    s.add_argument("--out", default=None, help="결과 CSV 저장 경로")
    s.add_argument("--limit", type=int, default=20, help="표에 출력할 건수")
    s.add_argument("--min-awards", type=int, default=None, help="최소 낙찰 건수")
    s.add_argument("--max-distance", type=float, default=None, help="근접 점수 만점 기준 거리(km)")
    s.add_argument("--strict", action="store_true",
                   help="인접 업종까지 제외 (기본은 '동일 업종'부터 제외)")
    s.add_argument("--no-demand-orgs", action="store_true", help="발주기관은 후보에서 뺀다")
    s.add_argument("--include-excluded", action="store_true", help="CSV 에 제외 후보도 담는다")
    s.add_argument("-v", "--verbose", action="store_true")

    pr = sub.add_parser("probe", help="어떤 API 경로가 살아있고 건수가 잡히는지 진단")
    pr.add_argument("--days", type=int, default=7, help="진단에 쓸 조회 기간(일)")
    pr.add_argument("--keyword", default="자동제어", help="검색 지원 여부를 볼 샘플 키워드")

    d = sub.add_parser("dart-lookup", help="상호로 DART 업종코드·주소를 조회 (설정값 검증용)")
    d.add_argument("names", nargs="+")

    sub.add_parser("show-config", help="현재 적용되는 제외 업종/타깃 업종을 출력")
    return p


def _cmd_screen(args) -> int:
    cfg = load_config(
        args.config,
        lookback_days=args.days,
        min_awards=args.min_awards,
        max_distance_km=args.max_distance,
        max_overlap_rank=0 if args.strict else None,
        include_demand_orgs=False if args.no_demand_orgs else None,
        g2b_service_key=os.environ.get("G2B_SERVICE_KEY") or None,
        dart_api_key=os.environ.get("DART_API_KEY") or None,
    )

    g2b_client = dart_client = None
    if not args.offline:
        from prime_contractor.sources.g2b import G2BClient, G2BError
        try:
            g2b_client = G2BClient(cfg.g2b_service_key)
        except G2BError as exc:
            print(f"[오류] {exc}\n      키 없이 확인만 하려면 --offline 을 쓰세요.", file=sys.stderr)
            return 2
        if cfg.dart_api_key:
            from prime_contractor.sources.dart import DartClient, DartError
            try:
                dart_client = DartClient(cfg.dart_api_key)
            except DartError as exc:
                print(f"[경고] DART 보강 생략: {exc}", file=sys.stderr)

    result = run_screen(cfg, offline=args.offline, g2b_client=g2b_client, dart_client=dart_client)
    print(render_table(result, limit=args.limit))

    if args.out:
        path = write_csv(result, args.out, include_excluded=args.include_excluded)
        print(f"\nCSV 저장: {path}")
    return 0


def _cmd_probe(args) -> int:
    from prime_contractor.sources.g2b import G2BClient, G2BError
    cfg = load_config(None, g2b_service_key=os.environ.get("G2B_SERVICE_KEY") or None)
    try:
        client = G2BClient(cfg.g2b_service_key)
    except G2BError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 2
    print("■ 나라장터 낙찰정보 API 진단")
    for line in client.diagnose(cfg.categories, sample_keyword=args.keyword, days=args.days):
        print(line)
    print("\n해석: '전체 N건' 이 0이면 경로/기간 문제, 전체는 있는데 "
          "'키워드 검색 0건' 이면 공고명 검색 미지원입니다(자동으로 전체 수집으로 전환됩니다).")
    return 0


def _cmd_dart_lookup(args) -> int:
    from prime_contractor.sources.dart import DartClient, DartError
    key = os.environ.get("DART_API_KEY", "")
    try:
        client = DartClient(key)
    except DartError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 2
    for name in args.names:
        info = client.lookup(name)
        if not info:
            print(f"{name}: DART 미등록(비상장 등)")
            continue
        print(f"{name}: 업종코드 {info.get('induty_code')} / {info.get('adres')} / "
              f"대표 {info.get('ceo_nm')} / 설립 {info.get('est_dt')}")
    return 0


def _cmd_show_config(args) -> int:
    cfg = load_config(getattr(args, "config", None))
    inc = cfg.incumbent
    print(f"■ 제외 대상 (기존 원청): {inc.name}")
    print(f"  계열사 상호 접두: {', '.join(inc.affiliate_prefixes)}")
    print(f"  업종코드: {', '.join(inc.ksic_prefixes)}")
    print(f"  핵심 키워드: {', '.join(inc.core_keywords)}")
    print(f"  인접 키워드: {', '.join(inc.adjacent_keywords)}")
    print("\n■ 타깃 업종 (판넬 수요)")
    for s in sorted(cfg.sectors, key=lambda x: x.weight, reverse=True):
        note = f"  — {s.note}" if s.note else ""
        print(f"  [{s.weight:.2f}] {s.name}: {', '.join(s.keywords[:6])}…{note}")
    print(f"\n■ 나라장터 검색 키워드\n  {', '.join(cfg.keywords)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return {
        "screen": _cmd_screen,
        "probe": _cmd_probe,
        "dart-lookup": _cmd_dart_lookup,
        "show-config": _cmd_show_config,
    }[args.command](args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:      # `| head` 로 잘라 볼 때 나는 잡음
        sys.stderr.close()
