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
    cost = g.add_argument_group("손익분기로 목표 잡기 (--target 대신)")
    cost.add_argument("--fixed-cost", type=int, default=None,
                      help="월 고정비(원) — 인건비·임차료·경비·이자")
    cost.add_argument("--variable-ratio", default=None,
                      help="재료·외주비 비율 (60 또는 0.6)")
    cost.add_argument("--target-profit", type=int, default=0,
                      help="월 목표이익(원). 없으면 손익분기가 곧 목표")
    cost.add_argument("--annual-revenue", type=int, default=None,
                      help="재무제표 연 매출액(원) — 아래 두 개와 같이 쓰면 고정비·비율을 계산")
    cost.add_argument("--cost-of-sales", type=int, default=None, help="재무제표 매출원가(원)")
    cost.add_argument("--sga", type=int, default=None, help="재무제표 판매비와관리비(원)")
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

    gl = sub.add_parser("goal", help="최우선 목표: 원청 한 곳 의존도를 낮추려면 몇 곳에 연락해야 하나")
    gl.add_argument("--sales", default=None, help="매출 파일 (월평균 매출을 여기서 계산)")
    gl.add_argument("--revenue", type=int, default=None, help="월매출(원) — 매출 파일 대신")
    gl.add_argument("--target-dependency", default="70",
                    help="목표: 가장 큰 원청 비중을 이 % 밑으로 (기본 70)")
    gl.add_argument("--current-dependency", default="100",
                    help="지금 가장 큰 원청 비중 % (원청 한 곳이면 100, 기본)")
    gl.add_argument("--months", type=int, default=12, help="기한 개월 (기본 12)")
    gl.add_argument("--per-client", type=int, default=10_000_000,
                    help="새 원청 한 곳이 초기에 주는 월 발주(원), 기본 1천만")
    gl.add_argument("--fixed-cost", type=int, default=None, help="월 고정비 — 위험 계산용")
    gl.add_argument("--variable-ratio", default=None, help="재료·외주비 % — 위험 계산용")
    gl.add_argument("--cash", type=int, default=0, help="지금 쓸 수 있는 현금 — 몇 달 버티나")

    ld = sub.add_parser("leads", help="영업 진행 기록 (연락 → 미팅 → 등록 → 첫 수주)")
    ld_sub = ld.add_subparsers(dest="leads_cmd", required=True)
    ld_sub.add_parser("list", help="기록 보기")
    la = ld_sub.add_parser("add", help="회사 추가")
    la.add_argument("name")
    la.add_argument("--bizno", default="")
    lm = ld_sub.add_parser("move", help="단계 옮기기")
    lm.add_argument("name")
    lm.add_argument("stage", help="후보 / 연락함 / 미팅 / 서류제출 / 등록완료 / 견적요청 / 첫수주 / 보류")
    lm.add_argument("--next", default="", help="다음 할 일")
    lm.add_argument("--date", default="", help="다음 할 일 날짜 YYYY-MM-DD (비우면 1주 뒤)")
    lm.add_argument("--terms", default="", help="결제조건 메모")
    lm.add_argument("--monthly", type=int, default=0, help="첫 수주 뒤 월 발주(원)")

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


def _cost_model(args):
    """명령줄에서 비용 구조를 만든다. 아무것도 안 주면 None."""
    from prime_contractor.breakeven import CostModel, from_financials, parse_ratio
    financial = (args.annual_revenue, args.cost_of_sales, args.sga)
    if any(v is not None for v in financial):
        if not all(v is not None for v in financial):
            raise ValueError("재무제표로 계산하려면 --annual-revenue, --cost-of-sales, "
                             "--sga 세 개를 모두 넣어주세요.")
        model = from_financials(*financial)
        model.monthly_profit = args.target_profit or 0
        return model
    if args.fixed_cost is None and args.variable_ratio is None:
        return None
    if args.fixed_cost is None or args.variable_ratio is None:
        raise ValueError("손익분기를 계산하려면 --fixed-cost 와 --variable-ratio 가 "
                         "둘 다 필요합니다.")
    return CostModel(monthly_fixed=args.fixed_cost,
                     variable_ratio=parse_ratio(args.variable_ratio),
                     monthly_profit=args.target_profit or 0)


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

    try:
        model = _cost_model(args)
    except ValueError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 2

    if model is not None:
        book.apply_target(model.target, overwrite=True)
    elif args.target:
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
    if model is not None:
        from prime_contractor.report import render_breakeven
        print(render_breakeven(book, model))
        print()
    print(render_gap(book, record, plan))
    if args.out:
        print(f"\nCSV 저장: {write_csv(result, args.out)}")
    return 0


def _pct(text) -> float:
    from prime_contractor.breakeven import parse_ratio
    return parse_ratio(str(text))


def _cmd_goal(args) -> int:
    from prime_contractor.goal import GoalInputs, build_plan
    from prime_contractor.leads import LeadBook, progress_lines

    revenue = args.revenue
    if revenue is None and args.sales:
        from prime_contractor.sales import load_sales
        try:
            revenue = load_sales(args.sales).average_revenue()
        except (OSError, ValueError) as exc:
            print(f"[오류] 매출 파일을 읽지 못했습니다: {exc}", file=sys.stderr)
            return 2
    if not revenue:
        print("[오류] --sales 로 매출 파일을 주거나 --revenue 로 월매출을 넣어주세요.",
              file=sys.stderr)
        return 2

    margin = fixed = 0
    if args.fixed_cost and args.variable_ratio:
        fixed = args.fixed_cost
        margin = 1 - _pct(args.variable_ratio)
    try:
        inputs = GoalInputs(
            monthly_revenue=revenue, target_dependency=_pct(args.target_dependency),
            current_dependency=_pct(args.current_dependency), months=args.months,
            revenue_per_new_client=args.per_client, monthly_fixed=fixed,
            margin_ratio=margin, cash_on_hand=args.cash)
    except ValueError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 2

    plan = build_plan(inputs)
    print(f"■ 최우선 목표  (지금 월매출 {revenue / 1e4:,.0f}만원 기준)")
    print("\n".join("  " + l if l else "" for l in plan.summary()))
    print()
    print("\n".join("  " + l if l else "" for l in progress_lines(LeadBook.load(), plan)))
    print("\n  전환율·'한 곳당 월 발주'는 어림값입니다. 영업 기록이 쌓이면 실제 숫자가 나옵니다.")
    return 0


def _cmd_leads(args) -> int:
    from prime_contractor.leads import STAGES, Lead, LeadBook
    book = LeadBook.load()
    if args.leads_cmd == "add":
        lead, created = book.add(Lead(name=args.name, bizno=args.bizno))
        book.save()
        print(("추가했습니다: " if created else "이미 있습니다: ") + lead.name)
        return 0
    if args.leads_cmd == "move":
        try:
            lead = book.move(args.name, args.stage, next_action=args.next, next_date=args.date)
        except (KeyError, ValueError) as exc:
            print(f"[오류] {exc}", file=sys.stderr)
            return 2
        if args.terms:
            lead.payment_terms = args.terms
        if args.monthly:
            lead.monthly_revenue = args.monthly
        book.save()
        print(f"{lead.name}: {lead.stage}  (다음 할 일 {lead.next_date})")
        return 0

    if not book.leads:
        print("아직 기록이 없습니다.  예) python -m prime_contractor.cli leads add \"○○전기\"")
        return 0
    order = {s: i for i, s in enumerate(STAGES)}
    for lead in sorted(book.leads, key=lambda l: (-order.get(l.stage, -1), l.name)):
        late = "  ⚠ 미룸" if lead.is_overdue() else ""
        terms = f"  [{lead.payment_terms}]" if lead.payment_terms else ""
        print(f"{lead.stage:<6} {lead.name}{terms}  — {lead.next_action or '-'} "
              f"({lead.next_date or '날짜 없음'}){late}")
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
        "goal": _cmd_goal,
        "leads": _cmd_leads,
        "dart-lookup": _cmd_dart_lookup,
        "show-config": _cmd_show_config,
    }[args.command](args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:      # `| head` 로 잘라 볼 때 나는 잡음
        sys.stderr.close()
