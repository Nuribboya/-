"""수집 → 분석 → 리포트 저장 → 알림 을 한 번 실행한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .analyzer import ChannelAnalysis, analyze_channel, should_alert
from .collector import YouTubeCollector, build_youtube_service
from .config import ChannelConfig, Config
from .db import Database, utcnow
from .report import save_report

log = logging.getLogger(__name__)


@dataclass
class ChannelResult:
    channel: ChannelConfig
    analysis: ChannelAnalysis | None = None
    report_path: Path | None = None
    alerted: bool = False
    error: str | None = None


def make_notifier(cfg: Config):
    tg = cfg.telegram
    if not tg.get("enabled", True):
        return None
    token = cfg.secret("bot_token_env", "telegram", required=False)
    chat_id = cfg.secret("chat_id_env", "telegram", required=False)
    if not token or not chat_id:
        log.warning("텔레그램 토큰/챗 ID가 .env에 없어 알림을 건너뜁니다.")
        return None
    from .notifier import TelegramNotifier

    return TelegramNotifier(token, chat_id)


def _resolve_channel_id(db: Database, ch: ChannelConfig) -> str | None:
    if ch.id:
        return ch.id
    row = db.find_channel_by_handle(ch.handle)
    return row["channel_id"] if row else None


def run_check(cfg: Config, *, collect: bool = True, notify: bool = True,
              service=None, notifier=None, now: datetime | None = None) -> list[ChannelResult]:
    now = now or utcnow()
    tz = ZoneInfo(cfg.schedule["timezone"])
    request = (cfg.raw.get("report") or {}).get("claude_request")
    max_videos = cfg.youtube["max_videos_per_channel"]
    if notify and notifier is None:
        notifier = make_notifier(cfg)

    results: list[ChannelResult] = []
    with Database(cfg.db_path) as db:
        collector = None
        if collect:
            service = service or build_youtube_service(cfg.youtube_api_key)
            collector = YouTubeCollector(service, db, max_videos=max_videos)

        for ch in cfg.channels:
            res = ChannelResult(ch)
            results.append(res)
            try:
                if collector:
                    channel_id = collector.collect_channel(ch, now).channel_id
                else:
                    channel_id = _resolve_channel_id(db, ch)
                    if not channel_id:
                        raise LookupError("DB에 수집된 데이터가 없습니다. 먼저 수집을 실행하세요.")

                a = analyze_channel(db, channel_id, ch.analysis, now, tz, max_videos=max_videos)
                res.analysis = a
                res.report_path = save_report(a, cfg.reports_dir, tz, request)
                log.info("[%s] %s → %s", a.channel_title,
                         "둔화" if a.slowdown else (a.insufficient or "정상"), res.report_path)

                if notifier and should_alert(a, db, now):
                    notifier.send_alert(
                        a, tz, res.report_path if cfg.telegram.get("send_report_file") else None,
                        request,
                    )
                    db.add_alert(a.channel_id, now, " / ".join(a.reasons), a.drop_pct,
                                 str(res.report_path))
                    db.commit()
                    res.alerted = True
            except Exception as exc:  # 한 채널 실패가 다른 채널 처리를 막지 않게
                log.exception("[%s] 처리 실패", ch.label)
                res.error = f"{type(exc).__name__}: {exc}"

    if notifier:
        _send_side_messages(cfg, notifier, results, tz, now)
    return results


def _send_side_messages(cfg: Config, notifier, results: list[ChannelResult],
                        tz: ZoneInfo, now: datetime) -> None:
    lines = []
    errors = [r for r in results if r.error]
    if errors:
        lines.append("⚠️ YouTube 모니터 오류")
        lines += [f"- {r.channel.label}: {r.error[:300]}" for r in errors]
    if cfg.telegram.get("notify_on_every_check"):
        if lines:
            lines.append("")
        lines.append(f"🕒 정기 점검 {now.astimezone(tz):%m-%d %H:%M}")
        for r in results:
            if r.analysis:
                a = r.analysis
                change = f"{a.change_pct:+.1f}%" if a.change_pct is not None else (a.insufficient or "-")
                lines.append(f"- {a.channel_title}: {'🚨' if a.slowdown else '✅'} {change}")
    if lines:
        try:
            notifier.send_text("\n".join(lines))
        except Exception:
            log.exception("텔레그램 부가 메시지 전송 실패")
