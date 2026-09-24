"""APScheduler 기반 백그라운드 실행 (cron 식은 config.yaml의 schedule.cron)."""

from __future__ import annotations

import logging

from .config import Config
from .pipeline import run_check

log = logging.getLogger(__name__)


def build_scheduler(cfg: Config, blocking: bool = True):
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched_cfg = cfg.schedule
    tz = sched_cfg["timezone"]
    scheduler = (BlockingScheduler if blocking else BackgroundScheduler)(timezone=tz)
    trigger = CronTrigger.from_crontab(sched_cfg["cron"], timezone=tz)

    def job():
        try:
            run_check(cfg)
        except Exception:
            log.exception("정기 점검 실패")

    scheduler.add_job(job, trigger, id="yt_check", max_instances=1, coalesce=True,
                      misfire_grace_time=3600)
    return scheduler, job


def run_forever(cfg: Config) -> None:
    scheduler, job = build_scheduler(cfg, blocking=True)
    log.info("스케줄러 시작: cron='%s' (%s)", cfg.schedule["cron"], cfg.schedule["timezone"])
    if cfg.schedule.get("run_on_start", True):
        job()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("스케줄러 종료")
