from __future__ import annotations

import argparse
import time
from pathlib import Path

from newsshorts.audience import get_audience, rank_items, topic_score
from newsshorts.config import Config, load_config
from newsshorts.labels import is_rumor
from newsshorts.notify import notify_draft
from newsshorts.pipeline import list_drafts, load_draft, make_draft, save_draft
from newsshorts.sources import NewsItem, SeenStore, new_items

SAMPLE_ITEM = NewsItem(
    id="demo",
    source="데모뉴스",
    title="국회, 내년도 예산안 처리… 기초연금 월 2만 원 인상 포함",
    summary=(
        "국회는 오늘 본회의를 열고 내년도 예산안을 처리했다. "
        "예산안에는 65세 이상 기초연금 수급자의 월 지급액을 2만 원 올리는 내용이 담겼다. "
        "인상분은 내년 1월 지급분부터 적용된다. "
        "여당 원내대표는 '어르신 생활 안정을 위한 예산'이라고 말했다."
    ),
    link="https://example.com/news/demo",
)

SAMPLE_US_ITEM = NewsItem(
    id="demo-us",
    source="데모뉴스(미국)",
    title="Exclusive: White House weighing new tariffs on Korean steel, officials say",
    summary=(
        "The administration is considering raising tariffs on steel imports from South Korea, "
        "two officials familiar with the discussions said. No final decision has been made."
    ),
    link="https://example.com/news/demo-us",
    region="us",
)


def _process(item: NewsItem, config: Config, args) -> None:
    print(f"[new] {item.source}: {item.title}")
    draft = make_draft(item, config, use_claude=not args.no_claude, use_tts=not args.no_tts)
    meta = load_draft(draft)
    print(f"[draft] {draft / 'video.mp4'}  ({meta['duration']}s)  제목: {meta['script']['title']}")
    notify_draft(config, draft, meta["script"]["title"])


def cmd_watch(config: Config, args) -> None:
    seen = SeenStore(config.state_file)
    first_run = not Path(config.state_file).exists()
    if first_run and not args.include_backlog:
        new_items(config, seen, prime=True)
        print("[info] 첫 실행: 지금 피드에 있는 기사는 건너뛰고, 이후 새 기사부터 만듭니다.")
    audience = get_audience(config.audience)
    while True:
        items = [i for i in rank_items(new_items(config, seen), audience)
                 if topic_score(i, audience) >= config.min_topic_score]
        if config.skip_rumors:
            for i in filter(is_rumor, items):
                print(f"[skip] 미확인 소문 기사: {i.title}")
            items = [i for i in items if not is_rumor(i)]
        for item in items[: args.max_per_poll]:
            try:
                _process(item, config, args)
            except Exception as exc:
                print(f"[error] {item.title}: {exc}")
        if args.once:
            return
        time.sleep(config.poll_seconds)


def cmd_demo(config: Config, args) -> None:
    _process(SAMPLE_US_ITEM if args.us else SAMPLE_ITEM, config, args)


def cmd_list(config: Config, args) -> None:
    for d in list_drafts(config, args.status):
        meta = load_draft(d)
        print(f"{meta['status']:9} {d}  {meta['script']['title']}")


def cmd_approve(config: Config, args) -> None:
    from newsshorts.upload import upload_short

    draft = Path(args.draft)
    meta = load_draft(draft)
    script = meta["script"]
    # Title/description can be edited in draft.json before approving.
    video_id = upload_short(config, draft / meta["video"], script["title"],
                            f"{script['description']}\n\n원문: {meta['item']['link']}",
                            script["hashtags"])
    meta.update(status="uploaded", youtube_id=video_id)
    save_draft(draft, meta)
    print(f"[uploaded] https://youtube.com/shorts/{video_id}")


def cmd_reject(config: Config, args) -> None:
    draft = Path(args.draft)
    meta = load_draft(draft)
    meta["status"] = "rejected"
    save_draft(draft, meta)
    print(f"[rejected] {draft}")


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
    p.add_argument("--status", choices=["pending", "uploaded", "rejected"])
    for name in ("approve", "reject"):
        sub.add_parser(name).add_argument("draft")

    args = parser.parse_args()
    config = load_config(args.config)
    {"watch": cmd_watch, "demo": cmd_demo, "list": cmd_list,
     "approve": cmd_approve, "reject": cmd_reject}[args.command](config, args)


if __name__ == "__main__":
    main()
