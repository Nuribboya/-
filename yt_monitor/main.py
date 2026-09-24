"""YouTube 채널 성과 모니터 CLI.

    python main.py --check-now        # 지금 수집 + 분석 + 리포트 + (필요 시) 알림
    python main.py --check-now --dry-run   # 텔레그램 전송 없이 실행
    python main.py --analyze-only     # API 호출 없이 DB 데이터로만 분석/리포트
    python main.py --schedule         # APScheduler로 cron 주기 자동 실행
    python main.py --test-telegram    # 텔레그램 테스트 메시지
    python main.py --get-chat-id      # 봇과 대화한 채팅방의 chat_id 확인
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    # `python main.py` 로 직접 실행해도 패키지 import가 되도록
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "yt_monitor"

from yt_monitor.config import ConfigError, load_config  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.yaml"


def _print_results(results) -> None:
    for r in results:
        if r.error:
            print(f"❌ {r.channel.label}: {r.error}")
            continue
        a = r.analysis
        change = f"{a.change_pct:+.1f}%" if a.change_pct is not None else "-"
        state = "🚨 둔화" if a.slowdown else ("⏳ 판단 보류" if a.insufficient else "✅ 정상")
        extra = " (알림 전송)" if r.alerted else ""
        print(f"{state} {a.channel_title}: 최근/이전 변화율 {change}{extra}")
        for reason in a.reasons:
            print(f"    - {reason}")
        if a.insufficient:
            print(f"    - {a.insufficient}")
        print(f"    리포트: {r.report_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YouTube 채널 성과 모니터링 & 성장 둔화 알림")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-now", action="store_true", help="지금 한 번 수집/분석/알림")
    mode.add_argument("--analyze-only", action="store_true", help="API 호출 없이 DB로만 분석/리포트")
    mode.add_argument("--schedule", action="store_true", help="cron 주기로 계속 실행")
    mode.add_argument("--test-telegram", action="store_true", help="텔레그램 테스트 메시지 전송")
    mode.add_argument("--get-chat-id", action="store_true", help="봇에게 메시지 보낸 채팅방 ID 조회")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="설정 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 알림을 보내지 않음")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        cfg = load_config(args.config)

        if args.get_chat_id:
            from yt_monitor.notifier import fetch_chat_ids

            token = cfg.secret("bot_token_env", "telegram")
            chats = fetch_chat_ids(token)
            if not chats:
                print("조회된 대화가 없습니다. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
            for chat_id, name in chats:
                print(f"chat_id={chat_id}  ({name})")
            return 0

        if args.test_telegram:
            from yt_monitor.pipeline import make_notifier

            notifier = make_notifier(cfg)
            if notifier is None:
                print("텔레그램 설정(.env의 토큰/챗 ID 또는 telegram.enabled)을 확인하세요.")
                return 1
            notifier.send_text("✅ YouTube 모니터 텔레그램 연결 테스트 성공!")
            print("테스트 메시지를 보냈습니다.")
            return 0

        if args.schedule:
            from yt_monitor.scheduler import run_forever

            run_forever(cfg)
            return 0

        from yt_monitor.pipeline import run_check

        results = run_check(cfg, collect=not args.analyze_only, notify=not args.dry_run)
        _print_results(results)
        return 1 if results and all(r.error for r in results) else 0
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
