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
    generation: object | None = None          # generator.Generation
    generation_error: str | None = None


def make_notifier(cfg: Config):
    tg = cfg.telegram
    if not tg.get("enabled", True):
        return None
    token = cfg.secret("telegram", "bot_token", required=False)
    chat_id = cfg.secret("telegram", "chat_id", required=False)
    if not token or not chat_id:
        log.warning("텔레그램 봇 토큰/chat_id가 설정되지 않아 알림을 건너뜁니다.")
        return None
    from .notifier import TelegramNotifier

    return TelegramNotifier(token, chat_id)


def make_generator(cfg: Config):
    """둔화 감지 시 자동 생성을 쓸 때만 ScriptGenerator를 만든다."""
    o = cfg.ollama
    if not (o.get("enabled", True) and o.get("auto_generate_on_slowdown", True)):
        return None
    from .generator import ScriptGenerator

    return ScriptGenerator.from_config(cfg)


def _auto_generate(cfg: Config, generator, db: Database, a: ChannelAnalysis, tz: ZoneInfo,
                   now: datetime, res: ChannelResult) -> None:
    from .generator import record_generation, save_generation
    from .report import channel_context

    try:
        g = generator.generate(channel_id=a.channel_id, channel_title=a.channel_title,
                               context=channel_context(a, tz), now=now, trigger="auto")
        save_generation(g, cfg.outputs_dir, tz)
        record_generation(db, g)
        res.generation = g
        log.info("[%s] 주제/대본 생성 → %s", a.channel_title, g.output_path)
    except Exception as exc:  # Ollama가 꺼져 있어도 알림은 보내야 한다
        log.exception("[%s] 주제/대본 생성 실패", a.channel_title)
        res.generation_error = f"{type(exc).__name__}: {exc}"


def _resolve_channel_id(db: Database, ch: ChannelConfig) -> str | None:
    if ch.id:
        return ch.id
    row = db.find_channel_by_handle(ch.handle)
    return row["channel_id"] if row else None


def run_check(cfg: Config, *, collect: bool = True, notify: bool = True, generate: bool = True,
              service=None, notifier=None, generator=None,
              now: datetime | None = None) -> list[ChannelResult]:
    now = now or utcnow()
    tz = ZoneInfo(cfg.schedule["timezone"])
    request = (cfg.raw.get("report") or {}).get("claude_request")
    max_videos = cfg.youtube["max_videos_per_channel"]
    if notify and notifier is None:
        notifier = make_notifier(cfg)
    if generate and generator is None:
        generator = make_generator(cfg)
    if not generate:
        generator = None

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

                if (notifier or generator) and should_alert(a, db, now):
                    if generator:
                        _auto_generate(cfg, generator, db, a, tz, now, res)
                    if notifier:
                        try:
                            notifier.send_alert(
                                a, tz, res.report_path if cfg.telegram.get("send_report_file") else None,
                                request, generation=res.generation,
                                generation_error=res.generation_error,
                            )
                            res.alerted = True
                        except Exception as exc:
                            log.exception("[%s] 텔레그램 알림 전송 실패", a.channel_title)
                            res.error = f"텔레그램 전송 실패: {type(exc).__name__}: {exc}"
                    if not (res.alerted or res.generation):
                        continue  # 아무것도 못 했으면 다음 점검 때 다시 시도
                    # 알림/생성 이력 → 쿨다운 동안 같은 채널로 반복 생성·알림하지 않음
                    db.add_alert(a.channel_id, now, " / ".join(a.reasons), a.drop_pct,
                                 str(res.generation.output_path if res.generation else res.report_path))
                    db.commit()
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
