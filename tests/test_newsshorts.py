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
