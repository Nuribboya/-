"""성장세 분석.

영상별 지표 ("게시 후 경과일 대비 조회수 증가율")
- views_per_day : 현재 누적 조회수 / 게시 후 경과일
- views_at_age  : 게시 후 age_days일 시점 조회수 (수집 시계열을 선형 보간)
                  → 영상 나이가 달라서 생기는 편향이 없지만, 시계열이 쌓여야 쓸 수 있다.
                    데이터가 부족하면 자동으로 views_per_day로 대체한다.

채널 성장 둔화 판단
- 비교 가능한 영상(min_age_days 이상 경과)을 최신순으로 정렬
- 최근 N개 평균(또는 중앙값) vs 그 이전 M개 평균
- 하락률 = (이전 - 최근) / 이전 × 100 ≥ drop_threshold_pct 이면 둔화
- (선택) 채널 전체 조회수의 최근 7일 증가량이 그 전 7일 대비 weekly_drop_threshold_pct 이상 하락해도 둔화
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .db import Database, Snapshot, Video, from_iso

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]
_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "this", "that", "are", "how", "what",
    "shorts", "short", "ep", "vs", "feat", "official",
    "그리고", "하는", "있는", "없는", "이런", "그냥", "진짜", "정말", "너무", "하기", "하면",
}


# ---- 시계열 보조 함수 -------------------------------------------------------

def value_at(points: list[tuple[datetime, float]], t: datetime) -> float | None:
    """(시각, 값) 오름차순 목록에서 t 시점 값을 선형 보간. 범위 밖이면 None."""
    pts = [(ts, v) for ts, v in points if v is not None]
    if not pts or t < pts[0][0] or t > pts[-1][0]:
        return None
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            span = (t1 - t0).total_seconds()
            if span == 0:
                return float(v1)
            return v0 + (v1 - v0) * (t - t0).total_seconds() / span
    return float(pts[-1][1])  # t == 마지막 시점 (점이 1개인 경우 포함)


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 0:
        return None
    return (new - old) / old * 100.0


def _agg(values: list[float], how: str) -> float | None:
    if not values:
        return None
    return statistics.median(values) if how == "median" else statistics.fmean(values)


# ---- 결과 구조 --------------------------------------------------------------

@dataclass
class VideoPerf:
    video: Video
    age_days: float
    views: int | None
    likes: int | None
    comments: int | None
    views_per_day: float | None
    views_at_age: float | None
    gain_7d: float | None          # 최근 7일 동안 늘어난 조회수 (시계열 필요)
    is_short: bool
    metric: float | None = None    # 비교에 실제로 쓰인 값

    @property
    def like_rate(self) -> float | None:
        if not self.views or self.likes is None:
            return None
        return self.likes / self.views * 100

    @property
    def comment_rate(self) -> float | None:
        if not self.views or self.comments is None:
            return None
        return self.comments / self.views * 100


@dataclass
class ChannelAnalysis:
    channel_id: str
    channel_title: str
    analyzed_at: datetime
    settings: dict
    metric_used: str
    metric_note: str | None
    videos: list[VideoPerf]
    recent: list[VideoPerf]
    baseline: list[VideoPerf]
    recent_avg: float | None
    baseline_avg: float | None
    drop_pct: float | None                  # 양수 = 하락
    subscribers: int | None
    weekly_views: float | None              # 최근 7일 채널 조회수 증가량
    prev_weekly_views: float | None         # 그 전 7일
    weekly_change_pct: float | None
    top: list[VideoPerf]
    patterns: dict
    reasons: list[str] = field(default_factory=list)
    insufficient: str | None = None

    @property
    def slowdown(self) -> bool:
        return bool(self.reasons)

    @property
    def change_pct(self) -> float | None:
        return None if self.drop_pct is None else -self.drop_pct


# ---- 영상 지표 계산 ---------------------------------------------------------

def video_perf(video: Video, snaps: list[Snapshot], now: datetime, settings: dict) -> VideoPerf:
    last = snaps[-1] if snaps else None
    # 경과일은 마지막 수집 시점 기준 (조회수와 같은 시점이어야 비율이 맞다)
    ref = last.collected_at if last else now
    age_days = max((ref - video.published_at).total_seconds() / 86400, 1e-6)
    views = last.view_count if last else None
    points = [(s.collected_at, s.view_count) for s in snaps]

    at_age = None
    target = video.published_at + timedelta(days=settings["age_days"])
    if target <= now:
        at_age = value_at(points, target)

    gain_7d = None
    if last is not None and views is not None:
        week_ago = value_at(points, last.collected_at - timedelta(days=7))
        if week_ago is None and video.published_at >= last.collected_at - timedelta(days=7):
            week_ago = 0.0  # 7일 이내 게시 영상은 게시 시점 조회수 0에서 출발
        if week_ago is not None:
            gain_7d = views - week_ago

    return VideoPerf(
        video=video,
        age_days=age_days,
        views=views,
        likes=last.like_count if last else None,
        comments=last.comment_count if last else None,
        views_per_day=(views / age_days) if views is not None else None,
        views_at_age=at_age,
        gain_7d=gain_7d,
        is_short=bool(video.duration_seconds is not None
                      and video.duration_seconds <= settings["shorts_max_seconds"]),
    )


def _split(perfs: list[VideoPerf], attr: str, settings: dict) -> tuple[list[VideoPerf], list[VideoPerf]]:
    eligible = [
        p for p in perfs
        if p.age_days >= settings["min_age_days"] and getattr(p, attr) is not None
    ]
    for p in eligible:
        p.metric = getattr(p, attr)
    n, m = settings["recent_n"], settings["baseline_m"]
    return eligible[:n], eligible[n : n + m]


# ---- 상위 성과 패턴 ---------------------------------------------------------

def _keywords(title: str) -> set[str]:
    words = re.findall(r"[0-9A-Za-z가-힣]+", title.lower())
    return {w for w in words if len(w) >= 2 and w not in _STOPWORDS and not w.isdigit()}


def top_patterns(top: list[VideoPerf], all_perfs: list[VideoPerf], tz: ZoneInfo) -> dict:
    if not top:
        return {}
    kw = Counter(k for p in top for k in _keywords(p.video.title))
    min_hits = 2 if len(top) >= 2 else 1
    cats = Counter(p.video.category_name or "미분류" for p in top)
    local = [p.video.published_at.astimezone(tz) for p in top]
    weekdays = Counter(WEEKDAYS_KO[d.weekday()] for d in local)
    hours = Counter(d.hour for d in local)

    def avg_duration(ps):
        vals = [p.video.duration_seconds for p in ps if p.video.duration_seconds]
        return statistics.fmean(vals) if vals else None

    def avg(ps, attr):
        vals = [getattr(p, attr) for p in ps if getattr(p, attr) is not None]
        return statistics.fmean(vals) if vals else None

    return {
        "keywords": [k for k, c in kw.most_common(8) if c >= min_hits],
        "categories": cats.most_common(),
        "shorts_ratio": sum(p.is_short for p in top) / len(top),
        "channel_shorts_ratio": (sum(p.is_short for p in all_perfs) / len(all_perfs)) if all_perfs else None,
        "avg_duration_sec": avg_duration(top),
        "channel_avg_duration_sec": avg_duration(all_perfs),
        "avg_title_len": statistics.fmean(len(p.video.title) for p in top),
        "weekdays": weekdays.most_common(),
        "hours": hours.most_common(),
        "like_rate": avg(top, "like_rate"),
        "channel_like_rate": avg(all_perfs, "like_rate"),
    }


# ---- 채널 분석 --------------------------------------------------------------

def analyze_channel(db: Database, channel_id: str, settings: dict, now: datetime,
                    tz: ZoneInfo, max_videos: int | None = None) -> ChannelAnalysis:
    row = db.get_channel(channel_id)
    title = row["title"] if row else channel_id
    videos = db.get_videos(channel_id, limit=max_videos)
    perfs = [video_perf(v, db.get_snapshots(v.video_id), now, settings) for v in videos]

    metric_used, note = settings["metric"], None
    recent, baseline = _split(perfs, metric_used, settings)
    if metric_used == "views_at_age" and (
        len(recent) < settings["recent_n"] or len(baseline) < settings["min_baseline"]
    ):
        note = (f"게시 후 {settings['age_days']}일 시점 시계열이 아직 부족해 "
                f"views_per_day(누적 조회수/경과일)로 대체했습니다.")
        metric_used = "views_per_day"
        recent, baseline = _split(perfs, metric_used, settings)

    recent_avg = _agg([p.metric for p in recent], settings["aggregate"])
    baseline_avg = _agg([p.metric for p in baseline], settings["aggregate"])

    insufficient = None
    if len(recent) < settings["recent_n"] or len(baseline) < settings["min_baseline"]:
        insufficient = (f"비교 가능한 영상 부족 (최근 {len(recent)}/{settings['recent_n']}개, "
                        f"이전 {len(baseline)}/{settings['min_baseline']}개 이상 필요)")
    change = pct_change(recent_avg, baseline_avg)
    drop_pct = None if (insufficient or change is None) else -change

    # 채널 전체 조회수: 최근 7일 증가량 vs 그 전 7일
    cstats = db.get_channel_stats(channel_id)
    points = [(from_iso(r["collected_at"]), r["view_count"]) for r in cstats]
    subscribers = cstats[-1]["subscriber_count"] if cstats else None
    weekly = prev_weekly = weekly_change = None
    if points:
        latest_t = points[-1][0]
        v_now = value_at(points, latest_t)
        v_7 = value_at(points, latest_t - timedelta(days=7))
        v_14 = value_at(points, latest_t - timedelta(days=14))
        if v_now is not None and v_7 is not None:
            weekly = v_now - v_7
        if v_7 is not None and v_14 is not None:
            prev_weekly = v_7 - v_14
        weekly_change = pct_change(weekly, prev_weekly)

    ranked = sorted((p for p in perfs if p.age_days >= settings["min_age_days"]
                     and getattr(p, metric_used) is not None),
                    key=lambda p: getattr(p, metric_used), reverse=True)
    top = ranked[: settings["top_k"]]

    reasons = []
    if drop_pct is not None and drop_pct >= settings["drop_threshold_pct"]:
        reasons.append(
            f"최근 {len(recent)}개 영상 평균 조회수 증가율이 이전 {len(baseline)}개 대비 "
            f"{drop_pct:.1f}% 하락 (기준 {settings['drop_threshold_pct']}%)"
        )
    wt = settings.get("weekly_drop_threshold_pct")
    if wt is not None and weekly_change is not None and -weekly_change >= wt:
        reasons.append(f"채널 주간 조회수 증가량이 전주 대비 {-weekly_change:.1f}% 하락 (기준 {wt}%)")

    return ChannelAnalysis(
        channel_id=channel_id, channel_title=title, analyzed_at=now, settings=settings,
        metric_used=metric_used, metric_note=note, videos=perfs, recent=recent,
        baseline=baseline, recent_avg=recent_avg, baseline_avg=baseline_avg,
        drop_pct=drop_pct, subscribers=subscribers, weekly_views=weekly,
        prev_weekly_views=prev_weekly, weekly_change_pct=weekly_change, top=top,
        patterns=top_patterns(top, [p for p in perfs if p.views is not None], tz),
        reasons=reasons, insufficient=insufficient,
    )


def should_alert(analysis: ChannelAnalysis, db: Database, now: datetime) -> bool:
    """둔화 상태이면서, 쿨다운이 지났거나 직전 알림보다 하락이 더 심해졌으면 알린다."""
    if not analysis.slowdown:
        return False
    last = db.last_alert(analysis.channel_id)
    if last is None:
        return True
    elapsed = now - from_iso(last["created_at"])
    if elapsed >= timedelta(hours=analysis.settings["alert_cooldown_hours"]):
        return True
    prev = last["drop_pct"]
    return (analysis.drop_pct is not None and prev is not None
            and analysis.drop_pct >= prev + 10)
