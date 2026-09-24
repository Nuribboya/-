"""YouTube 채널 성과 모니터 + 로컬 LLM(Ollama) 주제/대본 생성.

    python main.py                         # GUI (exe를 더블클릭했을 때와 동일)
    python main.py --check-now             # 지금 수집 + 분석 + 리포트 + (둔화 시) 생성/알림
    python main.py --check-now --dry-run   # 텔레그램 전송 없이
    python main.py --check-now --no-generate   # Ollama 생성 없이
    python main.py --analyze-only          # API 호출 없이 DB 데이터로만 분석/리포트
    python main.py --generate @내채널       # 지정 채널의 주제/대본 생성 (수동)
    python main.py --check-ollama          # Ollama 서버/모델 확인
    python main.py --schedule              # 창 없이 주기 실행 (APScheduler)
    python main.py --test-telegram         # 텔레그램 테스트 메시지
    python main.py --get-chat-id           # 봇과 대화한 채팅방의 chat_id 확인

exe(창 모드)에서도 같은 옵션을 쓸 수 있습니다. 예) Windows 작업 스케줄러에
`YouTubeMonitor.exe --check-now` 등록. 이때 출력은 logs/yt_monitor.log 에 남습니다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

if __package__ in (None, ""):
    # `python main.py` 로 직접 실행해도 패키지 import가 되도록
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "yt_monitor"

from yt_monitor.config import ConfigError, load_config  # noqa: E402
from yt_monitor.paths import app_dir, default_config_path  # noqa: E402


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if sys.stderr is not None:  # 창 모드 exe에는 콘솔이 없다
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
    try:
        log_dir = app_dir() / "logs"
        log_dir.mkdir(exist_ok=True)
        fh = RotatingFileHandler(log_dir / "yt_monitor.log", maxBytes=2_000_000,
                                 backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    for noisy in ("googleapiclient", "httpx", "urllib3", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print_results(results) -> None:
    for r in results:
        if r.error:
            print(f"❌ {r.channel.label}: {r.error}")
        a = r.analysis
        if a is None:
            continue
        change = f"{a.change_pct:+.1f}%" if a.change_pct is not None else "-"
        state = "🚨 둔화" if a.slowdown else ("⏳ 판단 보류" if a.insufficient else "✅ 정상")
        extra = " (알림 전송)" if r.alerted else ""
        print(f"{state} {a.channel_title}: 최근/이전 변화율 {change}{extra}")
        for reason in a.reasons:
            print(f"    - {reason}")
        if a.insufficient:
            print(f"    - {a.insufficient}")
        print(f"    리포트: {r.report_path}")
        if r.generation is not None:
            print(f"    ✨ 주제/대본: {r.generation.output_path}")
        if r.generation_error:
            print(f"    ⚠️ 생성 실패: {r.generation_error}")


def run_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="YouTube 채널 성과 모니터 + Ollama 주제/대본 생성")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="GUI 실행 (인자가 없을 때 기본)")
    mode.add_argument("--check-now", action="store_true", help="지금 한 번 수집/분석/생성/알림")
    mode.add_argument("--analyze-only", action="store_true", help="API 호출 없이 DB로만 분석/리포트")
    mode.add_argument("--generate", metavar="채널", help="@핸들 또는 UC채널ID의 주제/대본 생성")
    mode.add_argument("--check-ollama", action="store_true", help="Ollama 서버/모델 확인")
    mode.add_argument("--schedule", action="store_true", help="창 없이 주기 실행")
    mode.add_argument("--test-telegram", action="store_true", help="텔레그램 테스트 메시지 전송")
    mode.add_argument("--get-chat-id", action="store_true", help="봇에게 메시지 보낸 채팅방 ID 조회")
    parser.add_argument("--config", default=str(default_config_path()), help="설정 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 알림을 보내지 않음")
    parser.add_argument("--no-generate", action="store_true", help="둔화 시 자동 생성을 하지 않음")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.gui or not any((args.check_now, args.analyze_only, args.generate, args.check_ollama,
                            args.schedule, args.test_telegram, args.get_chat_id)):
        from yt_monitor.gui import run_gui

        return run_gui(args.config)

    try:
        cfg = load_config(args.config)
        from yt_monitor.generator import ensure_prompts

        ensure_prompts(cfg)

        if args.check_ollama:
            from yt_monitor.ollama_client import OllamaClient

            st = OllamaClient(cfg.ollama["host"], cfg.ollama["model"]).status()
            print(st.message)
            return 0 if st.ok else 1

        if args.generate:
            from yt_monitor.generator import main as generator_main

            return generator_main(["--channel", args.generate, "--config", args.config])

        if args.get_chat_id:
            from yt_monitor.notifier import fetch_chat_ids

            chats = fetch_chat_ids(cfg.secret("telegram", "bot_token"))
            if not chats:
                print("조회된 대화가 없습니다. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
            for chat_id, name in chats:
                print(f"chat_id={chat_id}  ({name})")
            return 0

        if args.test_telegram:
            from yt_monitor.pipeline import make_notifier

            notifier = make_notifier(cfg)
            if notifier is None:
                print("텔레그램 설정(봇 토큰/chat_id 또는 telegram.enabled)을 확인하세요.")
                return 1
            notifier.send_text("✅ YouTube 모니터 텔레그램 연결 테스트 성공!")
            print("테스트 메시지를 보냈습니다.")
            return 0

        if args.schedule:
            from yt_monitor.scheduler import run_forever

            run_forever(cfg)
            return 0

        from yt_monitor.pipeline import run_check

        results = run_check(cfg, collect=not args.analyze_only, notify=not args.dry_run,
                            generate=not args.no_generate)
        _print_results(results)
        return 1 if results and all(r.error for r in results) else 0
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    # 콘솔 인코딩이 cp949여도 이모지 출력에서 죽지 않도록
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    return run_cli(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
