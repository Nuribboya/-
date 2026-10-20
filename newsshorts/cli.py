from __future__ import annotations

import argparse
from pathlib import Path

from newsshorts.config import Config, load_config
from newsshorts.notify import notify_draft
from newsshorts.pipeline import list_drafts, load_draft, publish_draft, reject_draft, takedown_draft
from newsshorts.runner import Watcher, process
from newsshorts.samples import SAMPLE_ITEM, SAMPLE_US_ITEM


def cmd_watch(config: Config, args) -> None:
    watcher = Watcher(config, use_claude=not args.no_claude, use_tts=not args.no_tts,
                      max_per_poll=args.max_per_poll, include_backlog=args.include_backlog)
    if args.once:
        watcher.poll_once()
    else:
        watcher.run()


def cmd_demo(config: Config, args) -> None:
    draft = process(SAMPLE_US_ITEM if args.us else SAMPLE_ITEM, config,
                    use_claude=not args.no_claude, use_tts=not args.no_tts)
    notify_draft(config, draft, load_draft(draft)["script"]["title"])


def cmd_list(config: Config, args) -> None:
    for d in list_drafts(config, args.status):
        meta = load_draft(d)
        print(f"{meta['status']:9} {d}  {meta['script']['title']}")


def cmd_approve(config: Config, args) -> None:
    video_id = publish_draft(config, Path(args.draft))
    print(f"[uploaded] https://youtube.com/shorts/{video_id}")


def cmd_takedown(config: Config, args) -> None:
    video_id = takedown_draft(config, Path(args.draft))
    print(f"[taken down] {video_id}")


def cmd_reject(config: Config, args) -> None:
    reject_draft(Path(args.draft))
    print(f"[rejected] {args.draft}")


def main() -> None:
    parser = argparse.ArgumentParser(description="뉴스 → 유튜브 쇼츠 반자동 생성기")
    parser.add_argument("--config", default="newsshorts/config.example.json")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("watch", "demo"):
        p = sub.add_parser(name)
        p.add_argument("--no-claude", action="store_true", help="Claude 없이 템플릿 대본 사용")
        p.add_argument("--no-tts", action="store_true", help="음성 없이 무음 트랙으로 초안 생성")
    sub.choices["demo"].add_argument("--us", action="store_true", help="미국 샘플 기사 사용")
    watch = sub.choices["watch"]
    watch.add_argument("--once", action="store_true", help="한 번만 확인하고 종료")
    watch.add_argument("--include-backlog", action="store_true",
                       help="첫 실행 때 피드에 이미 있는 기사도 영상으로 만들기")
    watch.add_argument("--max-per-poll", type=int, default=3)

    p = sub.add_parser("list")
    p.add_argument("--status", choices=["pending", "uploaded", "rejected", "taken_down"])
    for name in ("approve", "reject", "takedown"):
        sub.add_parser(name).add_argument("draft")

    args = parser.parse_args()
    config = load_config(args.config)
    {"watch": cmd_watch, "demo": cmd_demo, "list": cmd_list,
     "approve": cmd_approve, "reject": cmd_reject, "takedown": cmd_takedown}[args.command](config, args)


if __name__ == "__main__":
    main()
