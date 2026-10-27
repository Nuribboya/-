from __future__ import annotations

import argparse
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from resell_radar.config import Settings, app_dir, load_settings, save_settings
from resell_radar.krify import merge_korean, reconcile
from resell_radar.learning import Estimator, load_price_log, write_price_log_template
from resell_radar.models import Release
from resell_radar.models import fmt_kst
from resell_radar.notify import Telegram, find_chat_id
from resell_radar.pipeline import collect, evaluate, notify, write_report
from resell_radar.report import render_html
from resell_radar.scoring import PASS, ProfitSettings, score_release
from resell_radar.sources import manual
from resell_radar.storage import Store
from resell_radar.venues import apply_grades, venue_table
from resell_radar.tiers import find_collaborators, find_hot_silhouette, load_tiers

TASK_NAME = "ResellRadar"


def setup(home: Path) -> Settings:
    settings = load_settings(home / "settings.json") or Settings()
    print("=== 리셀 레이더 설정 ===")
    print("텔레그램 알림을 쓰려면 @BotFather 에서 봇을 만들고 받은 토큰을 붙여넣으세요.")
    print("(알림 없이 쓰려면 그냥 엔터)")
    token = input("봇 토큰: ").strip()
    if token:
        input("만든 봇에게 텔레그램에서 아무 메시지나 하나 보낸 뒤 엔터를 누르세요...")
        try:
            chat_id = find_chat_id(token)
        except Exception as exc:
            chat_id = None
            print(f"텔레그램 연결 실패: {exc}")
        if chat_id:
            settings.telegram_token, settings.telegram_chat_id = token, chat_id
            Telegram(token, chat_id).send("리셀 레이더 연결 완료 ✅")
            print("연결 완료 — 텔레그램으로 테스트 메시지를 보냈어요.")
        else:
            print("봇에게 온 메시지를 못 찾았어요. 나중에 '설정'을 다시 실행하세요.")

    value = input(f"최소 순수익(원) [{settings.min_profit:,}]: ").replace(",", "").strip()
    if value.isdigit():
        settings.min_profit = int(value)
    value = input(f"최소 수익률(%) [{settings.min_profit_ratio * 100:.0f}]: ").strip()
    if value.replace(".", "", 1).isdigit():
        settings.min_profit_ratio = float(value) / 100

    save_settings(home / "settings.json", settings)
    manual.ensure_template(home / "releases.csv")
    print(f"저장 완료: {home / 'settings.json'}")
    return settings


def build_estimator(home: Path, tiers: dict, store: Store) -> Estimator:
    def grade_of(name: str) -> str:
        return score_release(Release("log", name, name), tiers, ProfitSettings()).level

    return Estimator(load_price_log(home / "prices.csv", grade_of) + store.market_observations(), tiers)


def run(home: Path, quiet: bool = False, open_report: bool = True) -> int:
    settings = load_settings(home / "settings.json") or Settings()
    tiers = load_tiers(home / "tiers.json")
    manual.ensure_template(home / "releases.csv")
    if not (home / "prices.csv").exists():
        write_price_log_template(home / "prices.csv")

    store = Store(home / "resell_radar.db")
    try:
        releases, errors = collect(settings, home, store)
        by_source = {}
        for r in releases:
            by_source[r.source] = by_source.get(r.source, 0) + 1

        # From the wide feeds (US calendars, luck-d) keep collabs, known partners,
        # hot silhouettes and anything sold by raffle; plain colorways are noise here.
        def worth_tracking(r: Release) -> bool:
            name = r.name.lower()
            return bool(" x " in name or r.method == "draw" or find_collaborators(name, tiers)
                        or find_hot_silhouette(name, tiers))

        releases = [r for r in releases if r.source != "luckd" or worth_tracking(r)]
        releases = merge_korean(releases, {row[0] for row in manual.STARTER_ROWS})
        venue_history = store.venue_history()
        apply_grades(releases, tiers, venue_history)
        calendar = store.remember_calendar([r for r in releases if r.source == "us_calendar"])
        releases = [r for r in releases if r.source != "us_calendar"] + calendar
        releases, fx_factor, fx_pairs = reconcile(releases, store, keep=worth_tracking)
        estimator = build_estimator(home, tiers, store)
        results = evaluate(releases, tiers, settings, store, estimator)
        for res in results:
            if res.release.venues:
                store.record_venues(res.release.key, [v.name for v in res.release.venues], res.score.level)
        venues = venue_table(results, tiers, store.venue_history())
        sent = 0
        if settings.has_telegram:
            tg = Telegram(settings.telegram_token, settings.telegram_chat_id)
            try:
                sent = notify(results, settings, store, tg.send)
            except Exception as exc:
                errors.append(f"텔레그램 전송 실패: {exc}")
    finally:
        store.close()
    write_report(results, home / "latest_report.csv")
    report = home / "report.html"
    report.write_text(render_html(results, errors, datetime.now(timezone.utc), estimator, (fx_factor, fx_pairs), venues), encoding="utf-8")

    for err in errors:
        print(f"[경고] {err}")
    if quiet:
        return 0

    print("sources: " + " ".join(f"{k}={v}" for k, v in sorted(by_source.items())))
    lk = [r.release for r in results if r.release.source == "luckd"]
    grades = {}
    for row in venues:
        grades[row["grade"]] = grades.get(row["grade"], 0) + 1
    print(f"luckd kept={len(lk)} draw={sum(r.method == 'draw' for r in lk)} "
          f"with_venues={sum(bool(r.venues) for r in lk)} | venues by grade: "
          + " ".join(f"{g}={grades.get(g, 0)}" for g in "SABC"))
    shown = [r for r in results if r.score.verdict != PASS]
    print(f"\n수집 {len(results)}건 → 추천/애매 {len(shown)}건 (패스 {len(results) - len(shown)}건)\n")
    for r in shown:
        s, rel = r.score, r.release
        profit = f"{s.est_profit:,}원({s.price_basis})" if s.est_profit is not None else "-"
        print(f"[{s.verdict} {s.level}] {rel.name}")
        print(f"    예상순수익 {profit} | 응모마감 {fmt_kst(rel.entry_end)} | {' / '.join(s.reasons)}")
    print(f"\n결과 화면: {report}")
    if open_report:
        webbrowser.open(report.as_uri())
    if settings.has_telegram:
        print(f"텔레그램 알림 {sent}건 전송")
    return 0


def schedule(hours: int) -> int:
    if sys.platform != "win32":
        print("자동 실행 등록은 윈도우에서만 지원해요.")
        return 1
    if not getattr(sys, "frozen", False):
        # Task Scheduler starts in System32, so only the .exe (whose data folder
        # is fixed next to itself) finds its settings reliably.
        print("자동 실행 등록은 exe 버전에서만 지원해요.")
        return 1
    cmd = ["schtasks", "/Create", "/F", "/SC", "HOURLY", "/MO", str(hours), "/TN", TASK_NAME,
           "/TR", f'"{sys.executable}" run --quiet']
    done = subprocess.run(cmd, capture_output=True, text=True)
    print(done.stdout or done.stderr)
    return done.returncode


def unschedule() -> int:
    done = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], capture_output=True, text=True)
    print(done.stdout or done.stderr)
    return done.returncode


def menu(home: Path) -> int:
    """What a double-click on the .exe opens."""
    if load_settings(home / "settings.json") is None:
        setup(home)
    while True:
        print("\n1) 지금 확인하기   2) 설정   3) 3시간마다 자동 실행 등록   4) 자동 실행 해제")
        print("5) 마지막 결과 화면 열기   0) 종료")
        choice = input("선택: ").strip()
        if choice == "1":
            run(home)
        elif choice == "2":
            setup(home)
        elif choice == "3":
            schedule(3)
        elif choice == "4":
            unschedule()
        elif choice == "5":
            if (home / "report.html").exists():
                webbrowser.open((home / "report.html").as_uri())
            else:
                print("아직 결과가 없어요. 1번으로 먼저 확인하세요.")
        elif choice == "0":
            return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="ResellRadar", description="한정판 발매 일정 수집 + 응모 가치 판정")
    sub = parser.add_subparsers(dest="command")
    run_p = sub.add_parser("run", help="수집 → 판정 → 알림 1회 실행")
    run_p.add_argument("--quiet", action="store_true", help="콘솔 출력 생략 (자동 실행용)")
    run_p.add_argument("--no-open", action="store_true", help="결과 화면을 브라우저로 열지 않음")
    sub.add_parser("setup", help="텔레그램/기준값 설정")
    sch = sub.add_parser("schedule", help="윈도우 작업 스케줄러에 자동 실행 등록")
    sch.add_argument("--hours", type=int, default=3)
    sub.add_parser("unschedule", help="자동 실행 해제")
    args = parser.parse_args(argv)

    home = app_dir()
    if args.command == "run":
        return run(home, quiet=args.quiet, open_report=not args.no_open)
    if args.command == "setup":
        setup(home)
        return 0
    if args.command == "schedule":
        return schedule(args.hours)
    if args.command == "unschedule":
        return unschedule()
    return menu(home)


if __name__ == "__main__":
    sys.exit(main())
