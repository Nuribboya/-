from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from newsshorts.audience import get_audience, rank_items, topic_score
from newsshorts.breaking import RecentStore, auto_publish_blockers
from newsshorts.config import Config
from newsshorts.labels import is_rumor
from newsshorts.log import log
from newsshorts.notify import notify_draft, notify_text
from newsshorts.pipeline import auto_published_today, load_draft, make_draft, publish_draft, save_draft
from newsshorts.sources import NewsItem, SeenStore, new_items

# Serialises draft creation/publishing between the watcher thread and app requests.
work_lock = threading.RLock()


def process(item: NewsItem, config: Config, use_claude: bool = True, use_tts: bool = True) -> Path:
    log(f"[new] {item.source}: {item.title}")
    with work_lock:
        draft = make_draft(item, config, use_claude=use_claude, use_tts=use_tts)
    meta = load_draft(draft)
    log(f"[draft] {draft.name}  ({meta['duration']}s)  제목: {meta['script']['title']}")
    return draft


class Watcher:
    """Polls the sources, drafts new items, and auto-publishes the ones that qualify."""

    def __init__(self, config: Config, use_claude: bool = True, use_tts: bool = True,
                 max_per_poll: int = 3, include_backlog: bool = False):
        self.config = config
        self.use_claude = use_claude
        self.use_tts = use_tts
        self.max_per_poll = max_per_poll
        self.audience = get_audience(config.audience)
        self.seen = SeenStore(config.state_file)
        self.recent = RecentStore(config.recent_file, config.auto_window_minutes)
        self.last_poll: str | None = None
        self._stop = threading.Event()
        self._poll_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        if not Path(config.state_file).exists() and not include_backlog:
            new_items(config, self.seen, prime=True)
            log("[info] 첫 실행: 지금 피드에 있는 기사는 건너뛰고, 이후 새 기사부터 만듭니다.")

    def try_auto_publish(self, draft: Path, notify_hold: bool = True) -> None:
        """Publish a pending draft right away if it clears every auto-publish check."""
        config = self.config
        with work_lock:
            meta = load_draft(draft)
            if meta["status"] != "pending":
                return
            item = NewsItem(**meta["item"])
            blockers = auto_publish_blockers(item, meta, self.recent, config, self.audience,
                                             auto_published_today(config))
            meta["auto_blockers"] = blockers
            save_draft(draft, meta)
            title = meta["script"]["title"]
            if blockers:
                if config.auto_publish:
                    log(f"[hold] 승인 대기: {'; '.join(blockers)}")
                if notify_hold:
                    notify_draft(config, draft, title)
                return
            video_id = publish_draft(config, draft, auto=True)
        log(f"[auto-published] https://youtube.com/shorts/{video_id}")
        notify_text(config, f"자동 공개됨\n{title}\nhttps://youtube.com/shorts/{video_id}\n\n"
                            f"내리기: python -m newsshorts.cli takedown {draft}")

    def poll_once(self) -> None:
        with self._poll_lock:
            self._poll()

    def _poll(self) -> None:
        config, audience = self.config, self.audience
        fresh = new_items(config, self.seen)
        self.recent.prune()
        for i in fresh:
            self.recent.add(i)
        items = [i for i in rank_items(fresh, audience)
                 if topic_score(i, audience) >= config.min_topic_score]
        if config.skip_rumors:
            for i in filter(is_rumor, items):
                log(f"[skip] 미확인 소문 기사: {i.title}")
            items = [i for i in items if not is_rumor(i)]
        for item in items[: self.max_per_poll]:
            try:
                existing = self.recent.existing_draft(item)
                if existing:
                    # Same story already drafted: don't make a second video, but another
                    # outlet confirming it may now make the first draft eligible.
                    log(f"[dup] 같은 소식 추가 보도({item.source}): {item.title}")
                    draft = Path(existing)
                else:
                    draft = process(item, config, self.use_claude, self.use_tts)
                    self.recent.mark_draft(item, draft)
                self.try_auto_publish(draft, notify_hold=not existing)
            except Exception as exc:
                log(f"[error] {item.title}: {exc}")
        self.recent.save()
        self.last_poll = datetime.now().isoformat(timespec="seconds")
        log(f"[poll] 확인 완료: 새 기사 {len(fresh)}건, 처리 대상 {min(len(items), self.max_per_poll)}건")

    def run(self, after: threading.Thread | None = None) -> None:
        # A replaced watcher may still be finishing a poll; don't overlap with it.
        if after is not None:
            after.join()
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                log(f"[error] 확인 중 오류: {exc}")
            self._stop.wait(self.config.poll_seconds)

    # Background control, used by the app.
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, after: threading.Thread | None = None) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, args=(after,), daemon=True)
        self._thread.start()
        log("[info] 감시 시작")

    def stop(self) -> None:
        self._stop.set()
        log("[info] 감시 중지")
