"""유행 쇼츠 분석 테스트 (가짜 YouTube API + 가짜 Ollama)."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from yt_monitor.config import LANGUAGE_PRESETS, apply_language, load_config, read_raw, save_config  # noqa: E402
from yt_monitor.db import Database  # noqa: E402
from yt_monitor.trends import (TrendCollector, TrendError, hot_keywords, run_trends,  # noqa: E402
                               title_matches, trend_context)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
KST = ZoneInfo("Asia/Seoul")
KO = {"popular_pages": 1, **LANGUAGE_PRESETS["ko"]["trends"], "min_views": 10000}


class _Req:
    def __init__(self, fn, kw):
        self.fn, self.kw = fn, kw

    def execute(self):
        return self.fn(**self.kw)


class _Res:
    def __init__(self, fn):
        self.fn = fn

    def list(self, **kw):
        return _Req(self.fn, kw)


def video(vid, title, hours_ago, views, seconds=40, channel="C1", tags=(), live="none"):
    return {"id": vid, "snippet": {"title": title, "channelId": channel, "channelTitle": f"채널{channel}",
                                   "publishedAt": (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "categoryId": "24", "tags": list(tags), "liveBroadcastContent": live},
            "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "2"},
            "contentDetails": {"duration": f"PT{seconds // 60}M{seconds % 60}S"}}


class FakeTrendYouTube:
    region = "KR"

    def __init__(self):
        self.popular = [
            video("p1", "편의점 신상 꿀조합 #shorts", 10, 500_000, tags=["편의점", "꿀조합"]),
            video("p2", "10분짜리 긴 영상", 5, 900_000, seconds=600),                 # 길어서 제외
            video("p3", "일주일 전 영상 편의점", 24 * 7, 3_000_000),                  # 오래돼서 제외
            video("p4", "English only title", 3, 400_000),                          # 한글 없음 제외
        ]
        self.searched = {"s1": video("s1", "편의점 라면 꿀조합 3가지", 20, 2_000_000, channel="C2",
                                     tags=["편의점", "라면"]),
                         "s2": video("s2", "작은 채널 떡상 편의점 리뷰", 6, 360_000, channel="C3"),
                         "s3": video("s3", "조회수 적은 영상", 2, 500, channel="C3"),       # min_views 미만
                         "s4": video("s4", "라이브 예고", 1, 50_000, live="upcoming")}
        self.calls = []

    def videos(self):
        def fn(part, maxResults, chart=None, regionCode=None, pageToken=None, id=None):
            self.calls.append(("videos", chart or id))
            if chart:
                assert chart == "mostPopular" and regionCode == self.region
                return {"items": self.popular}
            return {"items": [self.searched[i] for i in id.split(",") if i in self.searched]}
        return _Res(fn)

    def search(self):
        def fn(**kw):
            self.calls.append(("search", kw.get("q", "")))
            assert kw["order"] == "viewCount" and kw["videoDuration"] == "short"
            assert kw["publishedAfter"] == "2026-09-22T12:00:00Z"
            return {"items": [{"id": {"videoId": v}} for v in self.searched]}
        return _Res(fn)

    def channels(self):
        def fn(part, id, maxResults):
            subs = {"C1": "1000000", "C2": "1500000", "C3": "2000"}
            return {"items": [{"id": c, "statistics": {"subscriberCount": subs[c]}} for c in id.split(",")]}
        return _Res(fn)

    def videoCategories(self):
        def fn(part, regionCode):
            return {"items": [{"id": "24", "snippet": {"title": "엔터테인먼트"}}]}
        return _Res(fn)


def test_collect_filters_and_ranks():
    fake = FakeTrendYouTube()
    col = TrendCollector(fake, KO)
    vids = col.collect(NOW)
    assert [v.video_id for v in vids] == ["s1", "s2", "p1"]      # 시간당 조회수: 10만 > 6만 > 5만
    assert vids[0].views_per_hour(NOW) == pytest.approx(100_000)
    assert vids[1].subscribers == 2000 and vids[1].breakout == pytest.approx(180)
    assert vids[0].category == "엔터테인먼트"
    assert ("search", "") in fake.calls and ("search", "#shorts") in fake.calls
    assert col.units == 1 + 200 + 1 + 1 + 1                       # 인기 1 + 검색 2×100 + 상세 + 구독자 + 카테고리


def test_context_mentions_breakout_and_keywords():
    vids = TrendCollector(FakeTrendYouTube(), KO).collect(NOW)
    assert ("편의점", 3) in hot_keywords(vids)
    ctx = trend_context(vids, NOW, KST, KO)
    assert "1. 편의점 라면 꿀조합 3가지 | 조회 200.0만 | 시간당 10.0만" in ctx
    assert "작은 채널인데 알고리즘을 탄 영상 1개" in ctx and "떡상" in ctx
    assert "[여러 영상에 반복되는 키워드]" in ctx


def make_cfg(tmp_path, url, lang="ko"):
    raw = apply_language(read_raw(tmp_path / "config.yaml"), lang)
    raw["channels"] = [{"handle": "@test"}]
    raw["youtube"]["api_key"] = "fake"
    raw["ollama"].update(host=url)
    raw["trends"]["popular_pages"] = 1
    raw["trends"]["min_views"] = 10000
    return load_config(save_config(raw, tmp_path / "config.yaml"), load_env=False)


def test_run_trends_generates_shorts_script(tmp_path):
    server, url, handler = start_fake_ollama()
    try:
        cfg = make_cfg(tmp_path, url)
        res = run_trends(cfg, service=FakeTrendYouTube(), now=NOW)
        g = res.generation
        assert g.trigger == "trend" and g.channel_title == "트렌드" and g.script_lines
        assert g.output_path == tmp_path / "outputs" / "2026-09-25" / "트렌드.md"
        md = g.output_path.read_text(encoding="utf-8")
        assert "최근 유행 쇼츠 분석" in md and "편의점 라면 꿀조합" in md
        prompts = [r["messages"][-1]["content"] for r in handler.requests_log]
        assert "최근 며칠 동안 한국에서 조회수가 가장 빠르게 오른 쇼츠" in prompts[0]   # trend_topics.txt
        assert "쇼츠 내레이션 대본" in prompts[1] and "약 50초" in prompts[1]            # shorts_script.txt
        assert (tmp_path / "prompts" / "trend_topics.txt").exists()
        with Database(cfg.db_path) as db:
            n = db.conn.execute("SELECT COUNT(*) FROM generations WHERE channel_id='trend'").fetchone()[0]
        assert n == 1
        assert "🔥" in res.table
    finally:
        server.shutdown()


def test_run_trends_without_results(tmp_path):
    fake = FakeTrendYouTube()
    fake.popular, fake.searched = [], {}
    with pytest.raises(TrendError, match="찾지 못했습니다"):
        run_trends(make_cfg(tmp_path, "http://127.0.0.1:9"), service=fake, now=NOW, generate=False)


# ---- 영어권(미국) 모드 ----------------------------------------------------------------

class FakeUSTrends(FakeTrendYouTube):
    region = "US"

    def __init__(self):
        super().__init__()
        self.popular = [video("u1", "5 Gas Station Snacks You NEED to Try", 10, 900_000, tags=["snacks", "food"]),
                        video("u2", "편의점 꿀조합", 5, 900_000),                                 # 한글 → 제외
                        video("u3", "Самый вкусный перекус", 5, 900_000)]                          # 키릴 → 제외
        self.searched = {"e1": video("e1", "Gas station snacks ranked from worst to best", 20, 3_000_000,
                                     channel="C2", tags=["snacks"]),
                         "e2": video("e2", "This tiny channel's snack hack went viral", 6, 600_000, channel="C3")}


def test_title_language_filter():
    assert title_matches("5 Gas Station Snacks You NEED to Try 🍫", "en")
    assert not title_matches("편의점 snacks", "en") and not title_matches("日本のお菓子 ranking", "en")
    assert not title_matches("Самый вкусный перекус", "en") and not title_matches("😂😂", "en")
    assert title_matches("편의점 꿀조합", "ko") and not title_matches("Snacks", "ko")
    assert title_matches("anything 무엇이든", "any")


def test_run_trends_english_us(tmp_path):
    server, url, handler = start_fake_ollama()
    try:
        cfg = make_cfg(tmp_path, url, lang="en")
        assert cfg.language == "en" and cfg.video["tts_voice"].startswith("en-US")
        res = run_trends(cfg, service=FakeUSTrends(), now=NOW)
        assert [v.video_id for v in res.videos] == ["e1", "e2", "u1"]
        assert "Shorts ranked by views per hour" in res.context and "3.0M" in res.context
        assert "breakout videos from small channels" in res.context
        prompts = [r["messages"][-1]["content"] for r in handler.requests_log]
        assert "Shorts strategist for the US market" in prompts[0]           # trend_topics_en.txt
        assert "한국어로" in prompts[0]                                         # 추천 이유는 한국어
        assert "American English" in prompts[1] and "about 125 words" in prompts[1]   # 50초 ≈ 125단어
        assert (tmp_path / "prompts" / "shorts_script_en.txt").exists()
    finally:
        server.shutdown()


def test_old_config_without_language_switches_to_english(tmp_path):
    """언어 설정이 생기기 전 exe가 저장한 config.yaml(한국어 값) → 영어/미국 프리셋으로 전환."""
    old = read_raw(tmp_path / "none.yaml")
    del old["language"]
    old["video"]["tts_voice"] = "ko-KR-SunHiNeural"
    old["trends"]["region"] = "KR"
    old["channels"] = [{"handle": "@x"}]
    path = save_config(old, tmp_path / "config.yaml")
    raw = read_raw(path)
    assert raw["language"] == "en" and raw["video"]["tts_voice"] == "en-US-GuyNeural"
    assert raw["trends"]["region"] == "US" and raw["video"]["subtitle_font"] == "Arial Black"
    # 언어가 저장된 뒤에는 사용자가 바꾼 값을 유지
    raw["video"]["tts_voice"] = "en-US-JennyNeural"
    save_config(raw, path)
    assert read_raw(path)["video"]["tts_voice"] == "en-US-JennyNeural"


def test_no_non_ascii_in_strftime_formats():
    """Windows의 strftime은 형식 문자열에 한글이 있으면 UnicodeEncodeError를 낸다 (실제로 CI에서 발생)."""
    import re as _re

    root = Path(__file__).resolve().parent.parent / "yt_monitor"
    bad = []
    for f in root.rglob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for fmt in _re.findall(r":(%[^}]*)}", line) + _re.findall(r"strftime\(\"([^\"]*)\"", line):
                if not fmt.isascii():
                    bad.append(f"{f.name}:{n}: {fmt}")
    assert not bad, bad
