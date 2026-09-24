from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from yt_monitor.analyzer import analyze_channel, should_alert, value_at
from yt_monitor.collector import YouTubeCollector, parse_duration
from yt_monitor.config import ConfigError, load_config
from yt_monitor.db import Database
from yt_monitor.notifier import build_alert_html, split_message
from yt_monitor.pipeline import run_check
from yt_monitor.report import claude_prompt, render_markdown

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


# ---- YouTube API 가짜 객체 ---------------------------------------------------

class _Req:
    def __init__(self, fn, kw):
        self.fn, self.kw = fn, kw

    def execute(self):
        return self.fn(**self.kw)


class _Resource:
    def __init__(self, fn):
        self.fn = fn

    def list(self, **kw):
        return _Req(self.fn, kw)


class FakeYouTube:
    """영상마다 '하루 조회수'를 정해 두고, now 시점 누적 조회수를 돌려준다."""

    def __init__(self, videos, now):
        # videos: [(video_id, title, published_at, daily_views, duration_sec, category_id)]
        self.data = videos
        self.now = now
        self.calls = []

    def channels(self):
        def fn(part, id=None, forHandle=None):
            self.calls.append("channels")
            total = sum(self._views(v) for v in self.data)
            return {"items": [{
                "id": "UC_TEST",
                "snippet": {"title": "테스트채널", "customUrl": "@test"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}},
                "statistics": {"subscriberCount": "1200", "viewCount": str(total), "videoCount": "20"},
            }]}
        return _Resource(fn)

    def playlistItems(self):
        def fn(part, playlistId, maxResults, pageToken=None):
            self.calls.append("playlistItems")
            vids = sorted(self.data, key=lambda v: v[2], reverse=True)
            start = int(pageToken or 0)
            page = vids[start : start + maxResults]
            nxt = start + maxResults
            resp = {"items": [{"contentDetails": {"videoId": v[0]}} for v in page]}
            if nxt < len(vids):
                resp["nextPageToken"] = str(nxt)
            return resp
        return _Resource(fn)

    def videos(self):
        def fn(part, id, maxResults):
            self.calls.append("videos")
            ids = id.split(",")
            items = []
            for vid, title, pub, daily, dur, cat in self.data:
                if vid in ids:
                    views = self._views((vid, title, pub, daily, dur, cat))
                    items.append({
                        "id": vid,
                        "snippet": {"title": title, "publishedAt": pub.isoformat().replace("+00:00", "Z"),
                                    "categoryId": cat, "tags": ["태그"], "liveBroadcastContent": "none"},
                        "statistics": {"viewCount": str(views), "likeCount": str(views // 25),
                                       "commentCount": str(views // 200)},
                        "contentDetails": {"duration": f"PT{dur // 60}M{dur % 60}S"},
                    })
            return {"items": items}
        return _Resource(fn)

    def videoCategories(self):
        def fn(part, id):
            self.calls.append("videoCategories")
            names = {"22": "People & Blogs", "24": "Entertainment"}
            return {"items": [{"id": c, "snippet": {"title": names[c]}} for c in id.split(",")]}
        return _Resource(fn)

    def _views(self, v):
        age = (self.now - v[2]).total_seconds() / 86400
        return int(max(age, 0) * v[3])


def make_videos(recent_daily, old_daily, n_recent=5, n_old=10):
    """최근 n_recent개는 recent_daily, 그 이전은 old_daily 조회수/일. 3일 간격 업로드."""
    vids = []
    for i in range(n_recent + n_old):
        pub = T0 - timedelta(days=2 + 3 * i)
        daily = recent_daily if i < n_recent else old_daily
        title = f"먹방 브이로그 {i}" if i % 2 else f"여행 꿀팁 {i}"
        vids.append((f"v{i:02d}", title, pub, daily, 45 if i % 3 == 0 else 600, "22" if i % 2 else "24"))
    return vids


def write_config(tmp_path: Path, extra: str = "") -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
channels:
  - handle: "@test"
analysis:
  recent_n: 5
  baseline_m: 10
  drop_threshold_pct: 30
{extra}
storage:
  db_path: data/test.db
  reports_dir: reports
telegram:
  enabled: false
""", encoding="utf-8")
    return cfg


class FakeNotifier:
    def __init__(self):
        self.alerts, self.texts = [], []

    def send_alert(self, a, tz, report_path=None, request=None):
        self.alerts.append((a, report_path))

    def send_text(self, text):
        self.texts.append(text)


# ---- 단위 테스트 ------------------------------------------------------------

def test_parse_duration():
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("PT45S") == 45
    assert parse_duration("P1DT1M") == 86460
    assert parse_duration("bogus") is None


def test_value_at_interpolates():
    pts = [(T0, 0), (T0 + timedelta(days=2), 200)]
    assert value_at(pts, T0 + timedelta(days=1)) == pytest.approx(100)
    assert value_at(pts, T0 - timedelta(days=1)) is None
    assert value_at(pts, T0 + timedelta(days=3)) is None


def test_config_requires_channel_and_env(tmp_path, monkeypatch):
    bad = tmp_path / "bad.yaml"
    bad.write_text("channels: []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad, load_env=False)

    cfg = load_config(write_config(tmp_path, "  aggregate: median"), load_env=False)
    assert cfg.channels[0].handle == "@test"
    assert cfg.channels[0].analysis["aggregate"] == "median"
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        _ = cfg.youtube_api_key
    monkeypatch.setenv("YOUTUBE_API_KEY", "abc")
    assert cfg.youtube_api_key == "abc"


def test_collector_stores_timeseries(tmp_path):
    cfg = load_config(write_config(tmp_path), load_env=False)
    fake = FakeYouTube(make_videos(100, 100), T0)
    with Database(":memory:") as db:
        col = YouTubeCollector(fake, db, max_videos=12)
        for h in (0, 6, 12):
            fake.now = T0 + timedelta(hours=h)
            res = col.collect_channel(cfg.channels[0], fake.now)
        assert res.channel_id == "UC_TEST" and res.video_count == 12
        snaps = db.get_snapshots("v00")
        assert len(snaps) == 3
        assert snaps[-1].view_count > snaps[0].view_count
        videos = db.get_videos("UC_TEST")
        assert videos[0].category_name in ("Entertainment", "People & Blogs")
        assert len(db.get_channel_stats("UC_TEST")) == 3
    # 카테고리는 처음 한 번만 조회 (쿼터 절약)
    assert fake.calls.count("videoCategories") == 1


def _collect_days(db, cfg, fake, days, step_hours=6):
    col = YouTubeCollector(fake, db, max_videos=30)
    t = T0
    end = T0 + timedelta(days=days)
    while t <= end:
        fake.now = t
        col.collect_channel(cfg.channels[0], t)
        t += timedelta(hours=step_hours)
    return end


def test_detects_slowdown_and_patterns(tmp_path):
    cfg = load_config(write_config(tmp_path), load_env=False)
    fake = FakeYouTube(make_videos(recent_daily=40, old_daily=100), T0)
    with Database(":memory:") as db:
        end = _collect_days(db, cfg, fake, days=1)
        a = analyze_channel(db, "UC_TEST", cfg.channels[0].analysis, end, KST)
        assert len(a.recent) == 5 and len(a.baseline) == 10
        assert a.drop_pct == pytest.approx(60, abs=1)
        assert a.slowdown and "60" in a.reasons[0]
        assert should_alert(a, db, end)
        # 상위 영상은 이전 그룹(일평균 100)에서 나온다
        assert all(p.video.video_id not in {r.video.video_id for r in a.recent} for p in a.top)
        assert a.patterns["categories"]

        prompt = claude_prompt(a, KST)
        assert prompt.startswith("채널: 테스트채널")
        assert "최근 5개 영상 평균 조회수 증가율: -60" in prompt
        assert "다음 영상 주제" in prompt
        md = render_markdown(a, KST)
        assert "## 💬 Claude에 붙여넣기용" in md and "```text" in md
        assert "<b>" in build_alert_html(a, KST)


def test_no_slowdown_when_growing(tmp_path):
    cfg = load_config(write_config(tmp_path), load_env=False)
    fake = FakeYouTube(make_videos(recent_daily=150, old_daily=100), T0)
    with Database(":memory:") as db:
        end = _collect_days(db, cfg, fake, days=1)
        a = analyze_channel(db, "UC_TEST", cfg.channels[0].analysis, end, KST)
        assert a.change_pct == pytest.approx(50, abs=1)
        assert not a.slowdown and not should_alert(a, db, end)


def test_insufficient_data(tmp_path):
    cfg = load_config(write_config(tmp_path), load_env=False)
    fake = FakeYouTube(make_videos(40, 100, n_recent=3, n_old=1), T0)
    with Database(":memory:") as db:
        end = _collect_days(db, cfg, fake, days=0)
        a = analyze_channel(db, "UC_TEST", cfg.channels[0].analysis, end, KST)
        assert a.insufficient and a.drop_pct is None and not a.slowdown


def test_views_at_age_falls_back_then_uses_history(tmp_path):
    cfg = load_config(write_config(tmp_path, "  metric: views_at_age\n  age_days: 3\n  recent_n: 2\n  baseline_m: 2\n  min_baseline: 2"),
                      load_env=False)
    settings = cfg.channels[0].analysis
    # 수집 시작 이후 게시된 영상 4개: 앞의 2개는 하루 200, 뒤의 2개(최신)는 하루 50
    vids = [
        ("n0", "최신 A", T0 + timedelta(days=9), 50, 600, "22"),
        ("n1", "최신 B", T0 + timedelta(days=8), 50, 600, "22"),
        ("n2", "이전 A", T0 + timedelta(days=2), 200, 600, "24"),
        ("n3", "이전 B", T0 + timedelta(days=1), 200, 600, "24"),
    ]
    fake = FakeYouTube(vids, T0)
    with Database(":memory:") as db:
        end = _collect_days(db, cfg, fake, days=4)
        a = analyze_channel(db, "UC_TEST", settings, end, KST)
        assert a.metric_used == "views_per_day" and a.metric_note  # 아직 3일 시점 데이터 부족

        end = _collect_days(db, cfg, fake, days=15)
        a = analyze_channel(db, "UC_TEST", settings, end, KST)
        assert a.metric_used == "views_at_age" and a.metric_note is None
        assert a.recent[0].views_at_age == pytest.approx(150, rel=0.05)
        assert a.drop_pct == pytest.approx(75, abs=2)
        # 14일 이상 시계열이 있으므로 주간 증가량과 전주 비교도 계산된다
        assert a.weekly_views is not None and a.prev_weekly_views is not None


def test_alert_cooldown(tmp_path):
    cfg = load_config(write_config(tmp_path), load_env=False)
    fake = FakeYouTube(make_videos(40, 100), T0)
    with Database(":memory:") as db:
        end = _collect_days(db, cfg, fake, days=0)
        a = analyze_channel(db, "UC_TEST", cfg.channels[0].analysis, end, KST)
        db.add_alert("UC_TEST", end, "x", a.drop_pct, "")
        assert not should_alert(a, db, end + timedelta(hours=6))
        assert should_alert(a, db, end + timedelta(hours=25))


def test_split_message():
    text = "\n".join(["가" * 50] * 200)
    chunks = split_message(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks) == text
    assert split_message("x" * 2500, 1000) == ["x" * 1000, "x" * 1000, "x" * 500]


def test_run_check_end_to_end(tmp_path):
    cfg = load_config(write_config(tmp_path), load_env=False)
    fake = FakeYouTube(make_videos(40, 100), T0)
    notifier = FakeNotifier()
    results = run_check(cfg, service=fake, notifier=notifier, now=T0)
    r = results[0]
    assert r.error is None and r.alerted
    assert r.report_path.exists()
    assert r.report_path.parent.name == "2026-09-01"
    assert (tmp_path / "reports" / "latest" / r.report_path.name.split("_", 1)[1]).exists()
    assert len(notifier.alerts) == 1

    # 6시간 뒤 재실행: 쿨다운으로 알림 없음, 리포트는 새로 저장
    fake.now = T0 + timedelta(hours=6)
    results = run_check(cfg, service=fake, notifier=notifier, now=fake.now)
    assert not results[0].alerted and len(notifier.alerts) == 1
    assert len(list((tmp_path / "reports" / "2026-09-01").glob("*.md"))) == 2

    # --analyze-only: API 없이 DB로만
    results = run_check(cfg, collect=False, notifier=notifier, now=fake.now)
    assert results[0].error is None and results[0].analysis.slowdown


def test_run_check_reports_errors(tmp_path):
    cfg = load_config(write_config(tmp_path), load_env=False)

    class Broken(FakeYouTube):
        def channels(self):
            raise RuntimeError("quotaExceeded")

    notifier = FakeNotifier()
    results = run_check(cfg, service=Broken([], T0), notifier=notifier, now=T0)
    assert "quotaExceeded" in results[0].error
    assert notifier.texts and "오류" in notifier.texts[0]


def test_analyze_later_uses_snapshot_time(tmp_path):
    """수집 후 시간이 지나서 분석해도(--analyze-only) 비율이 왜곡되지 않아야 한다."""
    cfg = load_config(write_config(tmp_path), load_env=False)
    fake = FakeYouTube(make_videos(40, 100), T0)
    with Database(":memory:") as db:
        end = _collect_days(db, cfg, fake, days=1)
        a = analyze_channel(db, "UC_TEST", cfg.channels[0].analysis, end + timedelta(days=10), KST)
        assert a.drop_pct == pytest.approx(60, abs=1)


def test_scheduler_builds_from_cron(tmp_path):
    cfg = load_config(write_config(tmp_path), load_env=False)
    from yt_monitor.scheduler import build_scheduler

    scheduler, _ = build_scheduler(cfg, blocking=False)
    job = scheduler.get_jobs()[0]
    assert "hour='*/6'" in str(job.trigger)
