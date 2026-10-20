import json
import shutil

import pytest

from newsshorts.config import Config, Source, load_config
from newsshorts.render import _font, resolve_font, wrap
from newsshorts.script import generate_offline, split_sentences
from newsshorts.sources import NewsItem, SeenStore, matches_keywords, parse_feed
from newsshorts.tts import _align, _estimate_cues

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title>여야, 예산안 합의</title><link>https://example.com/a</link>
<description>&lt;p&gt;국회가 예산안을 처리했다. 여야가 합의했다.&lt;/p&gt;</description></item>
<item><title>오늘 날씨 맑음</title><link>https://example.com/b</link><description>전국 맑음</description></item>
</channel></rss>"""

ITEM = NewsItem(id="x", source="테스트", title="국회 예산안 처리", summary="첫 문장이다. 둘째 문장이다.",
                link="https://example.com/x")


def test_parse_feed_strips_html_and_ids_are_stable():
    items = parse_feed(Source("s", "u"), RSS)
    assert [i.title for i in items] == ["여야, 예산안 합의", "오늘 날씨 맑음"]
    assert items[0].summary == "국회가 예산안을 처리했다. 여야가 합의했다."
    assert parse_feed(Source("s", "u"), RSS)[0].id == items[0].id


def test_keyword_filter():
    items = parse_feed(Source("s", "u"), RSS)
    assert [i.title for i in items if matches_keywords(i, ["예산"])] == ["여야, 예산안 합의"]
    assert all(matches_keywords(i, []) for i in items)


def test_seen_store_persists(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.add("a")
    store.save()
    assert "a" in SeenStore(path)
    assert "b" not in SeenStore(path)


def test_offline_script_only_uses_feed_text():
    script = generate_offline(ITEM)
    assert script.title == ITEM.title
    assert "첫 문장이다." in script.sentences and "둘째 문장이다." in script.sentences
    assert ITEM.link in script.description


def test_split_sentences():
    assert split_sentences("하나다. 둘이다! 셋?") == ["하나다.", "둘이다!", "셋?"]


def test_cue_timing_is_monotonic():
    cues = _estimate_cues(["짧다.", "이건 조금 더 긴 문장입니다."])
    assert cues[0].end <= cues[1].start < cues[1].end
    aligned = _align(["가", "나다라"], [("x", 0, 1)], total=4.0)
    assert aligned[0].start == 0 and aligned[-1].end == pytest.approx(4.0)


def test_wrap_respects_width():
    font = _font(resolve_font(None), 60)
    lines = wrap("국회는 오늘 본회의를 열고 내년도 예산안을 처리했다", font, 400)
    assert len(lines) > 1
    assert all(font.getlength(line) <= 400 for line in lines)


def test_load_config(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"sources": [{"name": "a", "url": "u"}], "poll_seconds": 5}), encoding="utf-8")
    config = load_config(path)
    assert config.sources[0].name == "a" and config.poll_seconds == 5


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_make_draft_offline_end_to_end(tmp_path):
    from newsshorts.pipeline import list_drafts, load_draft, make_draft

    config = Config(output_dir=str(tmp_path))
    draft = make_draft(ITEM, config, use_claude=False, use_tts=False)
    meta = load_draft(draft)
    assert (draft / "video.mp4").stat().st_size > 0
    assert meta["status"] == "pending" and meta["duration"] > 1
    assert list_drafts(config, "pending") == [draft]


def test_senior_ranking_puts_relevant_topics_first():
    from newsshorts.audience import SENIOR, rank_items, topic_score

    sports = NewsItem(id="1", source="s", title="프로야구 개막전 결과", summary="", link="")
    pension = NewsItem(id="2", source="s", title="기초연금 내년 인상", summary="65세 이상 대상", link="")
    assert topic_score(sports, SENIOR) == 0
    assert topic_score(pension, SENIOR) > 0
    assert rank_items([sports, pension], SENIOR) == [pension, sports]


def test_unknown_audience_rejected():
    from newsshorts.audience import get_audience

    with pytest.raises(ValueError):
        get_audience("teen")


def test_senior_prompt_and_hashtags_reach_script():
    from newsshorts.audience import SENIOR
    from newsshorts.script import SYSTEM_PROMPT

    assert "50~70대" in SYSTEM_PROMPT + SENIOR.prompt
    assert generate_offline(ITEM, SENIOR).hashtags == list(SENIOR.default_hashtags)


US_SPEC = NewsItem(id="u", source="폴리티코", title="Exclusive: White House weighing new tariffs on Korea",
                   summary="Officials say no decision yet.", link="", region="us")


def test_labels_come_from_source_not_model():
    from newsshorts.labels import apply_labels, title_labels

    assert title_labels(US_SPEC) == ["단독", "미국", "전망"]
    assert title_labels(ITEM) == []
    # Model-invented tags and a bare "단독" are removed when the source doesn't justify them.
    assert apply_labels("[속보][단독] 단독 국회 예산안 처리", []) == "국회 예산안 처리"
    assert apply_labels("Exclusive: tariffs", ["단독"]) == "[단독] tariffs"
    assert apply_labels("백악관, 한국산 철강 관세 검토", ["단독", "미국", "전망"]) == \
        "[단독][미국][전망] 백악관, 한국산 철강 관세 검토"


def test_report_type_and_rumor_detection():
    from newsshorts.labels import is_rumor, report_type

    assert report_type(US_SPEC) == "전망"
    assert report_type(ITEM) == "보도"
    rumor = NewsItem(id="r", source="s", title="OO 의원 찌라시 확산", summary="", link="")
    assert is_rumor(rumor) and not is_rumor(ITEM)


def test_finalize_adds_attribution_once():
    from newsshorts.labels import finalize
    from newsshorts.script import Script

    script = finalize(Script(title="[속보] 관세 검토", quote="q", sentences=["관세를 검토 중입니다."]), US_SPEC)
    assert script.title.startswith("[단독][미국][전망]")
    assert script.sentences[0] == "폴리티코 보도입니다."
    assert finalize(script, US_SPEC).sentences.count("폴리티코 보도입니다.") == 1


def test_us_topics_score_case_insensitive():
    from newsshorts.audience import SENIOR, topic_score

    assert topic_score(US_SPEC, SENIOR) > 0


def _meta(ok=True, generator="claude"):
    return {"script": {"generator": generator}, "fact_check": {"ok": ok, "problems": [] if ok else ["x"]}}


BIG = NewsItem(id="b1", source="NPR", title="Trump announces new tariffs on Korean cars and steel imports",
               summary="The president signed the order on Tuesday.", link="", region="us")
BIG_2 = NewsItem(id="b2", source="폴리티코", title="Trump signs order imposing tariffs on Korean cars, steel",
                 summary="", link="", region="us")


def test_same_story_matches_different_wording_only():
    from newsshorts.breaking import same_story

    assert same_story(BIG.title, BIG_2.title)
    assert not same_story(BIG.title, "Senate passes farm bill after long debate")


def test_auto_publish_requires_corroboration_and_checks(tmp_path):
    from newsshorts.audience import SENIOR
    from newsshorts.breaking import RecentStore, auto_publish_blockers

    config = Config(auto_publish=True, recent_file=str(tmp_path / "r.json"))
    recent = RecentStore(config.recent_file, 90)
    recent.add(BIG)
    # One outlet only: held for a human.
    assert any("큰 소식 아님" in r for r in auto_publish_blockers(BIG, _meta(), recent, config, SENIOR, 0))
    recent.add(BIG_2)
    assert auto_publish_blockers(BIG, _meta(), recent, config, SENIOR, 0) == []
    # Every other check still holds it back.
    assert auto_publish_blockers(BIG, _meta(ok=False), recent, config, SENIOR, 0)
    assert auto_publish_blockers(BIG, _meta(generator="template"), recent, config, SENIOR, 0)
    assert auto_publish_blockers(BIG, _meta(), recent, config, SENIOR, config.auto_daily_limit)
    assert auto_publish_blockers(US_SPEC, _meta(), recent, config, SENIOR, 0)  # speculative
    assert auto_publish_blockers(BIG, _meta(), recent, Config(), SENIOR, 0)  # auto_publish off


def test_recent_store_dedupes_story_and_expires(tmp_path):
    from newsshorts.breaking import RecentStore

    recent = RecentStore(tmp_path / "r.json", 90)
    recent.add(BIG, now=1000.0)
    recent.mark_draft(BIG, tmp_path / "d1")
    recent.add(BIG_2, now=1100.0)
    assert recent.existing_draft(BIG_2) == str(tmp_path / "d1")
    recent.save()
    reloaded = RecentStore(tmp_path / "r.json", 90)
    reloaded.prune(now=1000.0 + 91 * 60)
    assert [e.id for e in reloaded.entries] == ["b2"]


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    # The app exports a configured key to the environment; keep it out of other tests.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from fastapi.testclient import TestClient

    from newsshorts.app import State, create_app

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"sources": [], "output_dir": str(tmp_path / "drafts"),
                               "state_file": str(tmp_path / "drafts/seen.json"),
                               "recent_file": str(tmp_path / "drafts/recent.json")}), encoding="utf-8")
    state = State(cfg, use_claude=False, use_tts=False, pin=None)
    return TestClient(create_app(state)), state


H = {"x-app": "1"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_app_draft_review_flow(app_client):
    client, _ = app_client
    name = client.post("/api/demo", headers=H).json()["name"]
    drafts = client.get("/api/drafts?status=pending").json()
    assert [d["name"] for d in drafts] == [name]
    assert client.get(f"/media/{name}/video.mp4").status_code == 200
    assert client.post(f"/api/drafts/{name}/update", json={"title": "새 제목"}, headers=H).status_code == 200
    assert client.get("/api/drafts").json()[0]["script"]["title"] == "새 제목"
    assert client.post(f"/api/drafts/{name}/reject", headers=H).status_code == 200
    assert client.get("/api/drafts?status=pending").json() == []


def test_app_blocks_cross_site_posts_and_path_escape(app_client):
    client, _ = app_client
    assert client.post("/api/watch/start").status_code == 403  # no x-app header
    assert client.get("/media/..%2F..%2Fetc/video.mp4").status_code == 404


def test_app_settings_roundtrip_masks_key(app_client):
    client, state = app_client
    body = {"auto_publish": True, "anthropic_api_key": "sk-ant-secret1234",
            "sources": [{"name": "NPR", "url": "https://feeds.npr.org/1014/rss.xml", "region": "us"}]}
    assert client.post("/api/config", json=body, headers=H).status_code == 200
    data = client.get("/api/config").json()["config"]
    assert data["auto_publish"] is True and data["anthropic_api_key"] == "…1234"
    assert state.config.sources[0].region == "us"
    # Sending the masked value back must not overwrite the stored key.
    client.post("/api/config", json={"anthropic_api_key": "…1234"}, headers=H)
    assert state.config.anthropic_api_key == "sk-ant-secret1234"
    bad = {"sources": [{"name": "x", "url": "u", "bogus": 1}]}
    assert client.post("/api/config", json=bad, headers=H).status_code == 400


def test_app_pin_required_in_lan_mode(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from newsshorts.app import State, create_app

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"output_dir": str(tmp_path / "d")}), encoding="utf-8")
    client = TestClient(create_app(State(cfg, False, False, pin="123456")))
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"x-pin": "123456"}).status_code == 200
