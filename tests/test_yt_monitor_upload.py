"""업로드 정보(제목 후보 · 카테고리 · 설명 · 해시태그) 테스트."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from yt_monitor.ollama_client import OllamaClient  # noqa: E402
from yt_monitor.trends import TrendVideo  # noqa: E402
from yt_monitor.upload_meta import (UploadMeta, category_hint, category_id, category_stats,  # noqa: E402
                                    generate_upload_meta, parse_upload_meta, render_upload_text)

PROMPTS = Path(__file__).resolve().parent.parent / "yt_monitor" / "prompts"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def tv(vid, cat, cat_name, views, hours=10):
    return TrendVideo(vid, "t", "c", "c", NOW - timedelta(hours=hours), 30, views,
                      category=cat_name, category_id=cat)


def test_category_id_accepts_names_and_numbers():
    assert category_id("24") == "24" and category_id(26) == "26" and category_id("26 Howto & Style") == "26"
    assert category_id("Education") == "27" and category_id("엔터테인먼트") == "24"
    assert category_id("Nonsense") is None and category_id(None) is None


def test_category_stats_from_trends():
    vids = [tv("a", "24", "Entertainment", 100_000), tv("b", "24", "Entertainment", 300_000),
            tv("c", "26", "Howto & Style", 1_000_000), tv("d", "", "", 5)]
    stats = category_stats(vids, NOW)
    assert [(s.category_id, s.count) for s in stats] == [("24", 2), ("26", 1)]
    assert stats[0].median_vph == 20_000
    assert "24 Entertainment: 2 videos, 20,000/h" in category_hint(stats, "en")
    assert "엔터테인먼트" in category_hint(stats, "ko")


def test_parse_upload_meta_cleans_values():
    meta = parse_upload_meta({"titles": ["한국어 제목", {"title": "\"Stop Scrolling\"", "why": "짧음"},
                                         "Stop scrolling"],
                              "best": 9, "category": "Education", "hashtags": ["facts", "#facts", "#Fun Facts"],
                              "tags": ["#a", "b"]},
                             lang="en", fallback_titles=["Fallback Title", "Other"])
    assert [t["title"] for t in meta.titles] == ["Stop Scrolling", "Fallback Title", "Other"]
    assert meta.best == 0 and meta.category_id == "27"
    assert meta.hashtags == ["#Shorts", "#facts", "#FunFacts"] and meta.tags == ["a", "b"]


def test_generate_upload_meta_with_ollama_and_fallback():
    server, url, handler = start_fake_ollama()
    try:
        meta = generate_upload_meta(OllamaClient(url, "qwen2.5:7b"), PROMPTS, lang="en", title="Snacks",
                                    script="You won't believe this.", trend_hint="[Categories] 26 Howto")
        prompt = handler.requests_log[-1]["messages"][-1]["content"]
        assert "You won't believe this." in prompt and "26 Howto" in prompt and "28: Science & Technology" in prompt
    finally:
        server.shutdown()
    # 영어 모드: 한국어 제목은 빠지고, 추천(best=3)이 그대로 가리킨다
    assert [t["title"] for t in meta.titles] == ["Gas Station Snack Hacks That Work",
                                                 "You've Been Eating Chips Wrong", "Snacks"]
    assert meta.title == "You've Been Eating Chips Wrong"
    assert meta.category_id == "26" and meta.hashtags[0] == "#Shorts" and meta.source == "ollama"

    # Ollama가 꺼져 있어도 예외 없이 기본값
    stats = category_stats([tv("x", "27", "Education", 1000)], NOW)
    fb = generate_upload_meta(OllamaClient("http://127.0.0.1:9", "m"), PROMPTS, lang="en", title="My Title",
                              script="s", topic={"topic": "t", "titles": ["Alt Title"]}, stats=stats)
    assert fb.source == "fallback" and [t["title"] for t in fb.titles] == ["My Title", "Alt Title"]
    assert fb.category_id == "27"
    # 영어 모드인데 제목이 한국어뿐이면 대본 첫 문장을 제목 후보로
    fb2 = generate_upload_meta(OllamaClient("http://127.0.0.1:9", "m"), PROMPTS, lang="en", title="한국어 제목",
                               script="You won't believe this. Second one.\nNext line.")
    assert fb2.title == "You won't believe this."


def test_render_upload_text():
    meta = UploadMeta([{"title": "Title A", "why": "이유"}, {"title": "Title B", "why": ""}], 1, "24",
                      "유행 1위", "Line one\nLine two", ["#Shorts", "#fun"], ["fun"])
    text = render_upload_text(meta, "Stock footage: Pexels (pexels.com)\n- someone")
    assert "⭐ 2) Title B" in text and "엔터테인먼트 (Entertainment)" in text
    assert "Line two\n\n#Shorts #fun\n\nStock footage: Pexels" in text
    assert "_출처.txt" not in text
    assert UploadMeta.from_dict(meta.to_dict()) == meta
