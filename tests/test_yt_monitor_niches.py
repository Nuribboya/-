"""콘텐츠 장르(익스트림 · 도파민) 테스트."""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from test_yt_monitor_trends import NOW, _Res, make_cfg, video  # noqa: E402
from yt_monitor.niches import apply_niche, matches_niche, niche_of  # noqa: E402
from yt_monitor.trends import TrendCollector, run_trends, trend_settings  # noqa: E402


class FakeExtremeYouTube:
    """스포츠 인기 목록 + 장르 검색어 결과."""

    def __init__(self):
        self.popular = [video("p1", "INSANE downhill line on a mountain bike", 10, 900_000, tags=["mtb"]),
                        video("p2", "NFL week 5 highlights", 5, 2_000_000, seconds=50),       # 장르 아님 → 제외
                        video("p3", "Paragliding over the Alps 🪂", 8, 600_000),
                        video("p4", "Big wave surfing at Nazaré 🌊", 8, 700_000)]      # 고르지 않은 종목 → 제외
        self.searched = {"s1": video("s1", "He almost didn't make it…", 20, 3_000_000, channel="C2"),
                         "s2": video("s2", "Storm chaser gets too close to a tornado", 30, 1_000_000,
                                     channel="C3")}
        self.calls = []

    def videos(self):
        def fn(part, maxResults, chart=None, regionCode=None, pageToken=None, id=None, videoCategoryId=None):
            self.calls.append(("videos", chart, videoCategoryId))
            if chart:
                return {"items": self.popular}
            return {"items": [self.searched[i] for i in id.split(",") if i in self.searched]}
        return _Res(fn)

    def search(self):
        def fn(**kw):
            self.calls.append(("search", kw.get("q", "")))
            assert kw["publishedAfter"] == (NOW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"items": [{"id": {"videoId": v}} for v in self.searched]}
        return _Res(fn)

    def channels(self):
        def fn(part, id, maxResults):
            return {"items": [{"id": c, "statistics": {"subscriberCount": "50000"}} for c in id.split(",")]}
        return _Res(fn)

    def videoCategories(self):
        def fn(part, regionCode):
            return {"items": [{"id": "24", "snippet": {"title": "Sports"}}]}
        return _Res(fn)


def test_niche_settings_and_matching():
    from yt_monitor.niches import label_of

    s = apply_niche({"niche": "extreme", "search_queries": ["", "#shorts"], "lookback_days": 3})
    assert s["search_queries"] == ["mountain bike downhill", "parkour", "snowboarding", "cliff diving", "paragliding"]
    assert s["lookback_days"] == 7 and s["popular_category"] == "17"
    assert apply_niche({"niche": "general", "search_queries": ["x"]})["search_queries"] == ["x"]
    assert niche_of({})["label"].startswith("액션 스포츠")                     # 기본 장르
    picked = apply_niche({"niche": "extreme", "sports": ["skydiving", "없는종목", "wingsuit"]})
    assert picked["search_queries"] == ["skydiving", "wingsuit"]
    assert label_of({"niche": "extreme", "sports": ["mtb"]}) == "액션 스포츠: 산악자전거"
    kw = niche_of({"niche": "extreme"})["keywords"]
    assert matches_niche("He sent the biggest drop in the bike park", [], kw)
    assert matches_niche("Watch this", ["parkour"], kw) and matches_niche("Paraglider vs storm", [], kw)
    assert not matches_niche("NFL week 5 highlights", ["football"], kw)
    assert not matches_niche("Tornado hits town", [], kw)                    # 종목 아님


def test_extreme_collect_and_prompts(tmp_path):
    server, url, handler = start_fake_ollama()
    try:
        cfg = make_cfg(tmp_path, url, lang="en")
        cfg.raw["trends"]["niche"] = "extreme"
        assert trend_settings(cfg)["popular_category"] == "17"
        fake = FakeExtremeYouTube()
        res = run_trends(cfg, service=fake, now=NOW)
        ids = [v.video_id for v in res.videos]
        assert "p2" not in ids and "p4" not in ids and {"p1", "p3", "s1", "s2"} <= set(ids)
        assert ("videos", "mostPopular", "17") in fake.calls
        assert [c[1] for c in fake.calls if c[0] == "search"] == trend_settings(cfg)["search_queries"]
        prompts = [r["messages"][-1]["content"] for r in handler.requests_log]
        assert "Channel niche: ACTION SPORTS" in prompts[0] and "mountain biking" in prompts[0]
        assert "No storms, animals" in prompts[0] and "Never tell viewers to try it" in prompts[0]
        assert "ACTION SPORTS" in prompts[1]                              # 대본에도 장르 지시
        assert "FIRST-PERSON POV" in res.generation.visual_hint and "gopro" in res.generation.visual_hint
    finally:
        server.shutdown()


def test_collector_without_niche_keywords_keeps_everything():
    from test_yt_monitor_trends import KO, FakeTrendYouTube

    vids = TrendCollector(FakeTrendYouTube(), KO).collect(NOW)
    assert len(vids) == 3


def test_climax_parse_and_suspense_prompt():
    from yt_monitor.niches import focus_text
    from yt_monitor.video.scenes import parse_climax

    assert parse_climax('{"mood": "dramatic", "climax": 5, "scenes": []}', 8) == 5
    assert parse_climax('{"climax": 12}', 8) is None and parse_climax("not json", 8) is None
    focus = focus_text({"niche": "extreme"}, "en")
    assert "what's at stake" in focus and "countdown" in focus and "Never reveal it early" in focus


def test_pov_focus_and_off_switch():
    from yt_monitor.niches import focus_text, visual_text

    assert "RIDER'S OWN INNER VOICE" in focus_text({"niche": "extreme"}, "en")
    assert "'나', 현재형" in focus_text({"niche": "extreme"}, "ko")
    assert "POV" in visual_text({"niche": "extreme"})
    off = {"niche": "extreme", "pov": False}
    assert "INNER VOICE" not in focus_text(off, "en") and "EVERY search keyword must show that sport" in visual_text(off)
