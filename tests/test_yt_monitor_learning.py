"""📈 내 채널 학습: 올린 영상 성과 → 다음 영상에 반영."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from test_yt_monitor_trends import make_cfg  # noqa: E402
from yt_monitor import channel_learning as L  # noqa: E402
from yt_monitor.db import Database  # noqa: E402
from yt_monitor.trends import run_topic  # noqa: E402

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
KST = ZoneInfo("Asia/Seoul")

# (제목, 길이, 48시간 조회수, 게시 KST 시각, 만든 영상 기록: (분위기, 음성))
VIDEOS = [
    ("5 Snacks You NEED To Try", 30, 90_000, 19, ("dramatic", "en-US-GuyNeural")),
    ("3 Gas Station Hacks That Work", 28, 70_000, 20, ("dramatic", "en-US-GuyNeural")),
    ("7 Weird Facts About Cats", 32, 60_000, 19, ("playful", "en-US-JennyNeural")),
    ("Why do cats knock things over", 55, 9_000, 9, ("calm", "en-US-EricNeural")),
    ("The history of bread", 57, 8_000, 10, ("calm", "en-US-EricNeural")),
    ("My morning routine", 50, 7_000, 9, None),
    ("Pancake art compilation", 45, 20_000, 14, ("playful", "en-US-JennyNeural")),
    ("Is this the best toast ever?", 40, 15_000, 15, None),
]


def fill_db(path: Path):
    with Database(path) as db:
        db.upsert_channel("UC1", "내 채널", "@me", None, NOW)
        for i, (title, dur, v48, hour_kst, prod) in enumerate(VIDEOS):
            pub = (NOW - timedelta(days=3 + i)).astimezone(KST).replace(hour=hour_kst, minute=0).astimezone(timezone.utc)
            db.upsert_video(f"v{i}", "UC1", title, pub, "24", dur, [], NOW)
            db.add_video_stats(f"v{i}", pub + timedelta(hours=30), int(v48 * 0.7), None, None)
            db.add_video_stats(f"v{i}", pub + timedelta(hours=60), int(v48 * 1.2), None, None)
            if prod:
                # 제목 후보 중 하나가 실제 올린 제목과 비슷함 (대소문자 · 문장부호 차이)
                db.add_production(pub - timedelta(hours=5), f"file {i}", [title.lower() + "!", "Other title"],
                                  mood=prod[0], voice=prod[1], duration=dur)
        # 아직 48시간이 안 지난 영상은 제외
        db.upsert_video("fresh", "UC1", "Brand new video", NOW - timedelta(hours=10), "24", 30, [], NOW)
        db.add_video_stats("fresh", NOW - timedelta(hours=1), 50_000, None, None)
        db.commit()


def test_learn_from_my_channel(tmp_path):
    fill_db(tmp_path / "db.sqlite")
    with Database(tmp_path / "db.sqlite") as db:
        mine = L.learn(db, ["UC1"], NOW, local_tz=KST)
    assert mine.n == 8 and mine.ready and mine.matched == 6
    v0 = next(v for v in mine.videos if v.video_id == "v0")
    # 48시간 조회수 = 30h(63,000)~60h(108,000) 보간 → 90,000
    assert round(v0.views_48h) == 90_000 and v0.production["mood"] == "dramatic"
    assert mine.winners[0] == "5 Snacks You NEED To Try" and "My morning routine" in mine.flops
    assert mine.best_seconds == 30
    num = next(p for p in mine.patterns if p["key"] == "number")
    assert num["lift"] > 3
    assert mine.moods[0]["name"] == "dramatic" and mine.moods[-1]["name"] == "calm"
    assert mine.hours[0]["start"] == 18 and mine.upload_times[0].startswith("18:00~21:00 (내 채널 기준")
    assert L.blend_seconds(50, mine) == 40 and L.blend_seconds(50, None) == 50
    assert "What worked on THIS channel" in L.prompt_text(mine, "en") and "Flopped" in L.prompt_text(mine, "en")
    assert "a number" in L.title_hint(mine, "en")
    assert "📈 내 채널 학습: 쇼츠 8개" in L.report_ko(mine) and "dramatic" in L.report_ko(mine)


def test_not_enough_videos(tmp_path):
    with Database(tmp_path / "db.sqlite") as db:
        mine = L.learn(db, ["UC1"], NOW, local_tz=KST)
    assert not mine.ready and L.prompt_text(mine) == "" and L.blend_seconds(45, mine) == 45
    assert "데이터 모으는 중 (0/6개)" in L.report_ko(mine)


def test_run_topic_uses_my_channel(tmp_path):
    server, url, handler = start_fake_ollama()
    try:
        cfg = make_cfg(tmp_path, url, lang="en")
        fill_db(cfg.db_path)
        # 방금 수집한 것처럼: 새로 수집하지 않는다 (YouTube를 부르면 테스트 실패)
        with Database(cfg.db_path) as db:
            db.add_video_stats("v0", NOW - timedelta(hours=1), 200_000, None, None)
            db.commit()
        res = run_topic(cfg, "Nutella food hacks", now=NOW)
        prompts = [r["messages"][-1]["content"] for r in handler.requests_log]
        assert "What worked on THIS channel" in prompts[0] and "5 Snacks You NEED To Try" in prompts[0]
        assert "about 40 seconds" in prompts[0]               # 기본 50초와 내 채널 30초의 평균
        upload_prompt = next(p for p in prompts if "category_id" in p)
        assert "[My channel]" in upload_prompt
        assert res.generation.upload["upload_times"][0].startswith("18:00~21:00 (내 채널 기준")
        assert "📈 내 채널 학습" in res.table
    finally:
        server.shutdown()
