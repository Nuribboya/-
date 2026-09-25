"""유행 쇼츠 심층 분석(숫자 · 제목 · 시간 · 썸네일) + 캐시 + 자동 반영 테스트."""

import base64
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from fake_video_services import start_fake_comfy  # noqa: E402  (썸네일 이미지 서버로 재사용: /view)
from test_yt_monitor_trends import NOW as TREND_NOW  # noqa: E402
from test_yt_monitor_trends import FakeUSTrends, make_cfg  # noqa: E402
from yt_monitor.ollama_client import OllamaClient  # noqa: E402
from yt_monitor.trend_insights import (analyze, analyze_thumbnails, load_cache, prompt_text,  # noqa: E402
                                       report_ko, save_cache, summarize_thumbs, title_hint)
from yt_monitor.trends import TrendVideo, run_topic, run_trends, trend_cache_path  # noqa: E402

NOW = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)          # 미국 동부 12시
KST = ZoneInfo("Asia/Seoul")


def tv(i, title, views, dur, hours_ago, likes=None, comments=None, thumb=""):
    return TrendVideo(f"v{i}", title, "c", "c", NOW - timedelta(hours=hours_ago), dur, views, likes, comments,
                      thumbnail=thumb)


def sample():
    # 20~30초 영상이 시간당 조회수가 높고, 숫자 제목이 잘 된다. 미국 동부 아침(6~9시)에 올라온 게 잘 뜸
    return [tv(1, "5 Snacks You NEED To Try", 2_000_000, 25, 4, 100_000, 5000),
            tv(2, "3 Gas Station Hacks", 1_500_000, 28, 4, 60_000, 900),
            tv(3, "I tried the viral toast", 900_000, 30, 3, 40_000, 800),
            tv(4, "Why is nobody talking about this?", 300_000, 55, 20, 9000, 100),
            tv(5, "The best sandwich", 250_000, 58, 22, 5000, 50),
            tv(6, "Pancake art", 200_000, 50, 21, 3000, 30)]


def test_analyze_numbers_titles_times():
    ins = analyze(sample(), NOW, region="US", local_tz=KST)
    assert ins.n == 6 and ins.duration_median == 40
    assert ins.top_duration_median == 30 and ins.recommended_seconds == 30        # 상위(최소 5개) 길이 → 5초 단위
    num = next(p for p in ins.patterns if p["key"] == "number")
    assert round(num["share"], 2) == 0.33 and num["lift"] > 5                      # 숫자 제목이 훨씬 잘 됨
    assert next(p for p in ins.patterns if p["key"] == "caps")["share"] > 0
    assert 0.02 < ins.like_rate < 0.06 and ins.top_reactions[0] == "5 Snacks You NEED To Try"
    assert ins.audience_tz == "America/New_York" and ins.best_hours[0]["start"] == 6    # 동부 6~9시에 올린 게 잘 뜸
    assert ins.upload_times[0] == "19:00~22:00 (한국 시간) = 미국 동부 06~09시"
    # 길이 자동값은 하한/상한 안
    assert analyze(sample()[3:], NOW, min_seconds=20, max_seconds=40).recommended_seconds == 40
    text = prompt_text(ins, "en")
    assert "about 30 seconds" in text and "a number" in text and "views/hour" in text
    assert "숫자 포함" in report_ko(ins) and "대본 목표 길이를 30초" in report_ko(ins)
    assert "Patterns that perform" in title_hint(ins, "en") and "잘 되는 패턴" in title_hint(ins, "ko")


def test_thumbnail_vision_and_cache(tmp_path):
    img, img_url, _ = start_fake_comfy(b"\x89PNG fake")
    server, url, handler = start_fake_ollama(models=["qwen2.5vl:7b"])
    try:
        vids = [tv(i, f"t{i}", 1000, 30, 2, thumb=f"{img_url}/view?i={i}") for i in range(3)]
        cache = {"v0": {"video_id": "v0", "title": "t0", "text_on_screen": "", "face": "none", "shot": "wide",
                        "emotion": "none", "hook": "cached", "subject": "", "colors": ""}}
        thumbs = analyze_thumbnails(vids, OllamaClient(url, "qwen2.5vl:7b"), cache=cache)
        assert len(thumbs) == 3 and thumbs[0]["hook"] == "cached"                  # 캐시는 다시 묻지 않음
        sent = [r for r in handler.requests_log if r["messages"][-1].get("images")]
        assert len(sent) == 2 and base64.b64decode(sent[0]["messages"][-1]["images"][0]) == b"\x89PNG fake"
        assert set(cache) == {"v0", "v1", "v2"}
        en, ko = summarize_thumbs(thumbs)
        assert "big text on screen 67%" in en and "DON'T EAT THIS" in en and "큰 글씨 67%" in ko
    finally:
        img.shutdown()
        server.shutdown()

    path = tmp_path / "cache.json"
    settings = {"region": "US", "min_views": 1}
    save_cache(path, settings, vids, cache, NOW)
    hit = load_cache(path, settings, NOW + timedelta(hours=2), 3)
    assert hit and [v.video_id for v in hit[0]] == ["v0", "v1", "v2"] and hit[1]["v1"]["hook"]
    assert hit[0][0].published_at == vids[0].published_at and hit[0][0].thumbnail == vids[0].thumbnail
    assert load_cache(path, settings, NOW + timedelta(hours=4), 3) is None          # 오래됨
    assert load_cache(path, {**settings, "region": "GB"}, NOW, 3) is None           # 설정이 다름


def test_run_trends_applies_insights_and_reuses_cache(tmp_path):
    server, url, handler = start_fake_ollama()          # 비전 모델 없음 → 썸네일 분석은 안내만
    try:
        cfg = make_cfg(tmp_path, url, lang="en")
        statuses = []
        res = run_trends(cfg, service=FakeUSTrends(), now=TREND_NOW, on_status=statuses.append)
        assert res.units > 0 and res.cached_at is None and trend_cache_path(cfg).exists()
        assert "📊 유행 쇼츠" in res.table and "What the numbers say" in res.context
        assert any("ollama pull qwen2.5vl:7b" in m for m in statuses)
        assert any("대본 목표 길이를 40초" in m for m in statuses)
        g = res.generation
        assert g.upload["upload_times"] and "Title patterns" in next(
            r["messages"][-1]["content"] for r in handler.requests_log if "category_id" in r["messages"][-1]["content"])

        # 캐시 재사용: YouTube를 부르지 않는다
        class Boom:
            def __getattr__(self, name):
                raise AssertionError("YouTube API를 다시 부르면 안 됨")
        statuses.clear()
        res2 = run_trends(cfg, service=Boom(), now=TREND_NOW + timedelta(hours=1), on_status=statuses.append,
                          generate=False)
        assert res2.units == 0 and res2.cached_at is not None and "쿼터 0" in statuses[0]
        assert [v.video_id for v in res2.videos] == [v.video_id for v in res.videos]

        # 주제 직접 입력도 최근 분석 결과를 반영 (쿼터 0)
        handler.requests_log.clear()
        res3 = run_topic(cfg, "Nutella food hacks", now=TREND_NOW + timedelta(hours=1))
        script_prompt = handler.requests_log[0]["messages"][-1]["content"]
        assert "about 100 words" in script_prompt and "What the numbers say" in script_prompt
        assert res3.generation.upload["upload_times"] and "반영했습니다" in res3.table
    finally:
        server.shutdown()


def test_visual_hint_reaches_scene_prompt():
    from yt_monitor.video.scenes import Scene, extract_keywords

    server, url, handler = start_fake_ollama()
    try:
        extract_keywords([Scene(1, "Snacks are wild.")], client=OllamaClient(url, "qwen2.5:7b"),
                         visual_hint="big text on screen 80%, a face 60%")
        prompt = handler.requests_log[-1]["messages"][-1]["content"]
        assert "첫 화면 스타일" in prompt and "big text on screen 80%" in prompt
    finally:
        server.shutdown()
