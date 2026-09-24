"""APScheduler 기반 주기 실행.

주기: config.yaml의 schedule.interval_hours(N시간 간격)가 있으면 그것을, 없으면 schedule.cron을 쓴다.
단독 실행: python main.py --schedule   (GUI에서는 '자동 체크' 토글이 BackgroundScheduler를 켜고 끈다)
"""

from __future__ import annotations

import logging
import re

from .config import Config
from .pipeline import run_check

log = logging.getLogger(__name__)


def make_trigger(cfg: Config):
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    sched_cfg = cfg.schedule
    tz = sched_cfg["timezone"]
    hours = sched_cfg.get("interval_hours")
    if hours:
        return IntervalTrigger(hours=float(hours), timezone=tz)
    return CronTrigger.from_crontab(sched_cfg["cron"], timezone=tz)


def describe_schedule(cfg: Config) -> str:
    hours = cfg.schedule.get("interval_hours")
    if hours:
        return f"{float(hours):g}시간마다"
    cron = cfg.schedule["cron"]
    m = re.fullmatch(r"0 \*/(\d+) \* \* \*", cron.strip())
    return f"{m.group(1)}시간마다" if m else f"cron '{cron}'"


def build_scheduler(cfg: Config, blocking: bool = True, job=None):
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.schedulers.blocking import BlockingScheduler

    tz = cfg.schedule["timezone"]
    scheduler = (BlockingScheduler if blocking else BackgroundScheduler)(timezone=tz)
    trigger = make_trigger(cfg)

    if job is None:
        def job():
            try:
                run_check(cfg)
            except Exception:
                log.exception("정기 점검 실패")

    scheduler.add_job(job, trigger, id="yt_check", max_instances=1, coalesce=True,
                      misfire_grace_time=3600)
    return scheduler, job


def run_forever(cfg: Config) -> None:
    _ = cfg.youtube_api_key  # 키가 없으면 몇 시간 뒤가 아니라 지금 바로 ConfigError
    scheduler, job = build_scheduler(cfg, blocking=True)
    log.info("스케줄러 시작: %s (%s)", describe_schedule(cfg), cfg.schedule["timezone"])
    if cfg.schedule.get("run_on_start", True):
        job()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("스케줄러 종료")
