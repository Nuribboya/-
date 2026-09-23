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

from prime_contractor import __version__
from prime_contractor.config import load_config
from prime_contractor.pipeline import filter_sector, run_industry_screen, run_screen
from prime_contractor.report import render_table, write_csv


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prime_contractor",
        description="자동제어 판넬 제조사를 위한 원청 후보 탐색 (기존 원청과 업종 중복 제외)",
    )
    p.add_argument("--version", action="version",
                   version=f"원청 찾기 v{__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("screen", help="원청 후보를 찾아 순위를 매긴다")
    s.add_argument("--offline", action="store_true", help="API 없이 샘플 데이터로 실행")
    s.add_argument("--days", type=int, default=None, help="조회 기간(일), 기본 180")
    s.add_argument("--config", default=None, help="설정 JSON 경로")
    s.add_argument("--out", default=None, help="결과 CSV 저장 경로")
    s.add_argument("--limit", type=int, default=20, help="표에 출력할 건수")
    s.add_argument("--min-awards", type=int, default=None, help="최소 낙찰 건수")
    s.add_argument("--max-distance", type=float, default=None, help="근접 점수가 0이 되는 거리(km)")
    s.add_argument("--within", type=float, default=None,
                   help="이 거리(km)를 넘는 후보는 목록에서 뺀다 (기본 70)")
    s.add_argument("--nationwide", action="store_true", help="거리 제한 없이 전국")
    s.add_argument("--sector", default=None,
                   help="이 업종만 본다 (일부만 적어도 됨, 예: 반도체 / 수처리)")
    s.add_argument("--min-grade", choices=["A", "B", "C", "D"], default=None,
                   help="이 등급 이상만 본다")
    s.add_argument("--explain", default=None, metavar="상호",
                   help="이 업체의 적합도 계산 내역을 자세히 출력 (일부만 적어도 됨)")
    s.add_argument("--exclude-same-industry", action="store_true",
                   help="KC그룹과 같은 업종(반도체 등)도 제외 — 기본은 계열사만 제외")
    s.add_argument("--strict", action="store_true", help="인접 업종까지 전부 제외")
    s.add_argument("--no-demand-orgs", action="store_true", help="발주기관은 후보에서 뺀다")
    s.add_argument("--keep-closed", action="store_true",
                   help="폐업으로 확인된 곳도 목록에 남긴다 (기본은 뺀다)")
    s.add_argument("--include-excluded", action="store_true", help="CSV 에 제외 후보도 담는다")
    s.add_argument("-v", "--verbose", action="store_true")

    pr = sub.add_parser("probe", help="어떤 API 경로가 살아있고 건수가 잡히는지 진단")
    pr.add_argument("--days", type=int, default=7, help="진단에 쓸 조회 기간(일)")
    pr.add_argument("--keyword", default="자동제어", help="검색 지원 여부를 볼 샘플 키워드")
    pr.add_argument("--dump", action="store_true", help="응답 1건의 실제 항목과 값을 그대로 출력")

    g = sub.add_parser("gap", help="매출 목표 미달분을 확인하고, 그만큼 채울 원청 후보를 고른다")
    g.add_argument("--sales", required=True,
                   help="매출 파일 (매출 앱 JSON 스냅샷 또는 연월,매출,목표 CSV)")
    g.add_argument("--month", default=None, help="볼 연월 (예: 2026-09). 기본은 마지막 마감월")
    g.add_argument("--target", type=int, default=None,
                   help="월 목표 금액(원). 매출 파일에 목표가 없을 때 쓴다")
    g.add_argument("--months-back", type=int, default=1,
                   help="부족분을 몇 달치로 볼지 (기본 1). 한 달만 보면 들쑥날쑥하다")
    g.add_argument("--offline", action="store_true", help="샘플 후보로 시험 실행")
    g.add_argument("--days", type=int, default=None, help="후보 조회 기간(일)")
    g.add_argument("--within", type=float, default=None, help="거리 상한(km)")
    g.add_argument("--nationwide", action="store_true")
    g.add_argument("--sector", default=None)
    g.add_argument("--min-grade", choices=["A", "B", "C", "D"], default=None)
    g.add_argument("--max-rows", type=int, default=12, help="접촉 후보 최대 개수")
    g.add_argument("--out", default=None, help="후보 CSV 저장 경로")
    g.add_argument("--config", default=None)
    g.add_argument("-v", "--verbose", action="store_true")

    i = sub.add_parser("industry-screen",
                       help="낙찰 이력과 무관하게 DART 상장사를 업종코드로 훑는다 "
                            "(민간 발주 원청을 찾을 때)")
    i.add_argument("--sector", default=None, help="이 업종만 본다 (예: 반도체)")
    i.add_argument("--within", type=float, default=None, help="거리 상한(km), 기본 70")
    i.add_argument("--nationwide", action="store_true", help="거리 제한 없이 전국")
    i.add_argument("--limit-companies", type=int, default=None,
                   help="확인할 상장사 수 상한 (시험 실행용)")
    i.add_argument("--limit", type=int, default=30, help="표에 출력할 건수")
    i.add_argument("--out", default=None, help="결과 CSV 저장 경로")
    i.add_argument("--config", default=None, help="설정 JSON 경로")
    i.add_argument("-v", "--verbose", action="store_true")

    d = sub.add_parser("dart-lookup", help="상호로 DART 업종코드·주소를 조회 (설정값 검증용)")
    d.add_argument("names", nargs="+")

    sub.add_parser("show-config", help="현재 적용되는 제외 업종/타깃 업종을 출력")
    return p


def _overlap_rank(args) -> int | None:
    """겹침 허용 등급. 아무 것도 안 주면 설정 기본값(계열사만 제외)."""
    if args.strict:
        return 0
    if getattr(args, "exclude_same_industry", False):
        return 1
    return None


def _within(args) -> float | None:
    if getattr(args, "nationwide", False):
        return None
    return args.within


def _cmd_gap(args) -> int:
    from prime_contractor.sales import load_sales, plan_to_close_gap
    from prime_contractor.report import render_gap

    try:
        book = load_sales(args.sales)
    except (OSError, ValueError) as exc:
        print(f"[오류] 매출 파일을 읽지 못했습니다: {exc}", file=sys.stderr)
        return 2
    if not book.months:
        print("[오류] 매출 기록이 비어 있습니다.", file=sys.stderr)
        return 2

    if args.target:
        book.apply_target(args.target)
    elif not book.has_targets:
        average = book.average_revenue()
        print(f"[안내] 매출 파일에 목표가 없습니다. --target 으로 월 목표를 넣어주세요.\n"
              f"       참고로 최근 평균 매출은 월 {average / 1e4:,.0f}만원입니다.",
              file=sys.stderr)
        return 2

    record = book.month(args.month) if args.month else book.latest_closed()
    if record is None:
        print(f"[오류] {args.month or '마감된 달'} 기록을 찾지 못했습니다.", file=sys.stderr)
        return 2

    gap = book.recent_gap(args.months_back) if args.months_back > 1 else record.gap
    cfg = load_config(
        args.config,
        lookback_days=args.days,
        within_km=_within(args),
        min_grade=args.min_grade,
        g2b_service_key=os.environ.get("G2B_SERVICE_KEY") or None,
        dart_api_key=os.environ.get("DART_API_KEY") or None,
    )
    if args.nationwide:
        from dataclasses import replace
        cfg = replace(cfg, within_km=None)

    g2b_client = dart_client = None
    if not args.offline:
        from prime_contractor.sources.g2b import G2BClient, G2BError
        try:
            g2b_client = G2BClient(cfg.g2b_service_key)
        except G2BError as exc:
            print(f"[오류] {exc}\n      키 없이 보려면 --offline 을 쓰세요.", file=sys.stderr)
            return 2
        if cfg.dart_api_key:
            from prime_contractor.sources.dart import DartClient, DartError
            try:
                dart_client = DartClient(cfg.dart_api_key)
            except DartError:
                pass

    result = run_screen(cfg, offline=args.offline,
                        g2b_client=g2b_client, dart_client=dart_client)
    if args.sector:
        filter_sector(result, args.sector)

    plan = plan_to_close_gap(gap, result.passed, lookback_days=cfg.lookback_days,
                             max_rows=args.max_rows)
    print(render_gap(book, record, plan))
    if args.out:
        print(f"\nCSV 저장: {write_csv(result, args.out)}")
    return 0


def _cmd_industry_screen(args) -> int:
    from prime_contractor.sources.dart import DartClient, DartError
    cfg = load_config(args.config, dart_api_key=os.environ.get("DART_API_KEY") or None)
    if args.nationwide:
        from dataclasses import replace
        cfg = replace(cfg, within_km=None)
    elif args.within is not None:
        from dataclasses import replace
        cfg = replace(cfg, within_km=args.within)
    try:
        client = DartClient(cfg.dart_api_key)
    except DartError as exc:
        print(f"[오류] {exc}\n      opendart.fss.or.kr 에서 인증키를 받아 "
              f"DART_API_KEY 에 넣어주세요.", file=sys.stderr)
        return 2

    result = run_industry_screen(cfg, client, limit=args.limit_companies)
    if args.sector:
        filter_sector(result, args.sector)
    print(render_table(result, limit=args.limit))
    if args.out:
        print(f"\nCSV 저장: {write_csv(result, args.out, include_excluded=False)}")
    return 0


def _cmd_screen(args) -> int:
    cfg = load_config(
        args.config,
        lookback_days=args.days,
        min_awards=args.min_awards,
        max_distance_km=args.max_distance,
        within_km=_within(args),
        max_overlap_rank=_overlap_rank(args),
        min_grade=args.min_grade,
        include_demand_orgs=False if args.no_demand_orgs else None,
        g2b_service_key=os.environ.get("G2B_SERVICE_KEY") or None,
        dart_api_key=os.environ.get("DART_API_KEY") or None,
        nts_service_key=os.environ.get("NTS_SERVICE_KEY") or None,
        drop_closed_businesses=False if args.keep_closed else None,
    )

    g2b_client = dart_client = nts_client = None
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
        if cfg.nts_service_key:
            from prime_contractor.sources.nts import NtsClient, NtsError
            try:
                nts_client = NtsClient(cfg.nts_service_key)
            except NtsError as exc:
                print(f"[경고] 사업자 상태 확인 생략: {exc}", file=sys.stderr)

    if args.nationwide:
        from dataclasses import replace
        cfg = replace(cfg, within_km=None)

    result = run_screen(cfg, offline=args.offline, g2b_client=g2b_client,
                        dart_client=dart_client, nts_client=nts_client)
    if args.sector:
        filter_sector(result, args.sector)

    if args.explain:
        return _print_explain(result, args.explain)

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
    for line in client.diagnose(cfg.categories, sample_keyword=args.keyword,
                                days=args.days, dump=args.dump):
        print(line)
    print("\n해석")
    print("  '전체 N건' 이 0      → 경로 또는 기간 문제")
    print("  '낙찰업체 추출 실패' → 그 오퍼레이션엔 업체명이 없음 (다른 쪽을 자동으로 씁니다)")
    print("  '키워드 검색 0건'    → 그 기간에 없었거나 공고명 검색 미지원")
    print("  값을 직접 보려면: python -m prime_contractor.cli probe --dump")
    return 0


def _print_explain(result, needle: str) -> int:
    """한 후보의 점수가 어떻게 나왔는지 축별로 펼쳐 본다."""
    pool = result.passed + result.excluded
    hits = [c for c in pool if needle in c.name]
    if not hits:
        print(f"'{needle}' 와(과) 맞는 후보가 없습니다. "
              f"(전체 {len(pool)}곳)", file=sys.stderr)
        return 1
    for c in hits[:5]:
        print(f"\n■ {c.name}  [{'원청 후보' if c.kind == 'contractor' else '발주처'}]")
        if c.fitness:
            print(c.fitness.explain())
        if c.overlap:
            print(f"  겹침 판정: {c.overlap.label} — {'; '.join(c.overlap.reasons)}")
        if c.awards:
            print("  수주 내역:")
            for a in c.awards[:6]:
                print(f"    - [{a.category}] {a.title} / {a.demand_org} / "
                      f"{a.amount / 1e8:.2f}억")
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
        "industry-screen": _cmd_industry_screen,
        "gap": _cmd_gap,
        "dart-lookup": _cmd_dart_lookup,
        "show-config": _cmd_show_config,
    }[args.command](args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:      # `| head` 로 잘라 볼 때 나는 잡음
        sys.stderr.close()
