"""Ollama 연동 · 주제/대본 생성 · 설정 저장 · exe 경로 처리 테스트 (가짜 Ollama 서버 사용)."""

import sys
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from test_yt_monitor import (  # noqa: E402
    KST, T0, FakeNotifier, FakeYouTube, make_videos, write_config,
)
from yt_monitor import paths  # noqa: E402
from yt_monitor.config import load_config, read_raw, save_config  # noqa: E402
from yt_monitor.db import Database  # noqa: E402
from yt_monitor.generator import (  # noqa: E402
    GenerationError, ScriptGenerator, generate_for_channel, load_template, parse_topics,
    render_template, tts_lines,
)
from yt_monitor.notifier import build_generation_html  # noqa: E402
from yt_monitor.ollama_client import ModelMissing, OllamaClient, OllamaUnavailable  # noqa: E402
from yt_monitor.pipeline import run_check  # noqa: E402

PROMPTS = Path(__file__).resolve().parent.parent / "yt_monitor" / "prompts"


@pytest.fixture
def ollama():
    server, url, handler = start_fake_ollama()
    yield url, handler
    server.shutdown()


def ollama_config(tmp_path, url, model="qwen2.5:7b"):
    cfg = load_config(write_config(tmp_path), load_env=False)
    cfg.raw["ollama"].update(host=url, model=model)
    cfg.raw["storage"]["prompts_dir"] = str(PROMPTS)
    cfg.raw["language"] = "ko"          # 가짜 Ollama가 한국어 대본을 돌려준다
    return cfg


# ---- 1) config ------------------------------------------------------------------

def test_secret_inline_and_env_override(tmp_path, monkeypatch):
    cfg = load_config(write_config(tmp_path, "youtube_extra: 1"), load_env=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    cfg.raw["youtube"]["api_key"] = "from-yaml"
    assert cfg.youtube_api_key == "from-yaml"
    monkeypatch.setenv("YOUTUBE_API_KEY", "from-env")
    assert cfg.youtube_api_key == "from-env"


def test_save_config_roundtrip(tmp_path):
    raw = read_raw(tmp_path / "없음.yaml")          # 파일 없으면 기본값
    assert raw["ollama"]["model"] == "qwen2.5:7b"
    raw["channels"] = [{"handle": "@내채널", "name": "메인"}]
    raw["youtube"]["api_key"] = "AIza-test"
    raw["schedule"]["interval_hours"] = 3
    path = save_config(raw, tmp_path / "config.yaml")
    assert "AIza-test" in path.read_text(encoding="utf-8")
    cfg = load_config(path, load_env=False)
    assert cfg.channels[0].label == "메인" and cfg.schedule["interval_hours"] == 3
    assert cfg.outputs_dir == tmp_path / "outputs"


def test_ensure_user_copy(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    (bundle / "prompts").mkdir(parents=True)
    (bundle / "prompts" / "a.txt").write_text("기본", encoding="utf-8")
    monkeypatch.setattr(paths, "resource_dir", lambda: bundle)
    app = tmp_path / "app"
    app.mkdir()
    target = paths.ensure_user_copy("prompts", base=app)
    assert (target / "a.txt").read_text(encoding="utf-8") == "기본"
    (target / "a.txt").write_text("수정", encoding="utf-8")
    paths.ensure_user_copy("prompts", base=app)      # 사용자가 고친 파일은 덮어쓰지 않음
    assert (target / "a.txt").read_text(encoding="utf-8") == "수정"


# ---- 3) Ollama 연동 ---------------------------------------------------------------

def test_ollama_status(ollama):
    url, _ = ollama
    assert OllamaClient(url, "qwen2.5:7b").status().ok
    missing = OllamaClient(url, "gemma2:9b").status()
    assert missing.server_up and not missing.model_present
    assert "ollama pull gemma2:9b" in missing.message
    with pytest.raises(ModelMissing):
        OllamaClient(url, "gemma2:9b").ensure_ready()
    with pytest.raises(OllamaUnavailable):
        OllamaClient("http://127.0.0.1:9", "qwen2.5:7b").ensure_ready()
    assert OllamaClient(url, "llama3").status().ok  # 태그 생략 = :latest


def test_ollama_chat_stream_and_plain(ollama):
    url, handler = ollama
    client = OllamaClient(url, "qwen2.5:7b")
    tokens = []
    streamed = client.chat("대본", on_token=tokens.append)
    assert len(tokens) > 1 and "".join(tokens) == streamed
    assert client.chat("대본") == streamed
    assert '"topics"' in client.chat("주제", json_mode=True)
    assert handler.requests_log[-1]["format"] == "json"


def test_render_template_keeps_json_braces():
    out = render_template('채널 {channel_name} {"topics": [{"topic": "x"}]} {unknown}',
                          {"channel_name": "A"})
    assert out == '채널 A {"topics": [{"topic": "x"}]} {unknown}'
    tpl = load_template(PROMPTS, "topics.txt")
    assert "{channel_data}" in tpl and '{"topics"' in tpl


def test_parse_topics_variants():
    topics, best = parse_topics('```json\n{"topics":[{"topic":"A","titles":["a1","a2","a3","a4"]},'
                                '{"주제":"B","제목":"b1","이유":"r"}],"best":2}\n```', 5, 3)
    assert [t["topic"] for t in topics] == ["A", "B"]
    assert topics[0]["titles"] == ["a1", "a2", "a3"]
    assert topics[1] == {"topic": "B", "reason": "r", "titles": ["b1"]}
    assert best == 1
    topics, best = parse_topics('설명... [{"topic":"C"}] 끝', 5, 3)
    assert topics[0]["titles"] == ["C"] and best == 0
    _, best = parse_topics('{"topics":[{"topic":"A"}],"best":9}', 5, 3)
    assert best == 0
    with pytest.raises(GenerationError):
        parse_topics("주제가 없습니다", 5, 3)


def test_tts_lines_cleans_markdown_and_splits_sentences():
    text = """## 오프닝
**내레이션:** 안녕하세요! 오늘은 특별한 날입니다. [BGM]
1. 첫째, 준비물을 챙기세요 🙂
(화면 전환)
---
제목: 무시될 줄
마지막으로 구독 부탁드려요."""
    assert tts_lines(text) == [
        "안녕하세요!", "오늘은 특별한 날입니다.", "첫째, 준비물을 챙기세요",
        "마지막으로 구독 부탁드려요.",
    ]


def test_generator_end_to_end_saves_files(ollama, tmp_path):
    url, handler = ollama
    gen = ScriptGenerator(OllamaClient(url, "qwen2.5:7b"), PROMPTS,
                          {"num_topics": 5, "num_titles": 3, "script_minutes": 2})
    g = gen.generate(channel_id="c1", channel_title="내 채널", context="채널 데이터 요약",
                     now=T0, trigger="manual")
    assert len(g.topics) == 3 and g.best == 1 and g.selected == 1
    assert g.title == "편의점 신상 조합 TOP5"
    assert g.script_lines[0] == "여러분, 편의점에서 이 조합 먹어보셨나요?"
    prompt = handler.requests_log[-1]["messages"][-1]["content"]
    assert "채널 데이터 요약" in prompt and "약 660자" in prompt and "{" not in prompt.split("[작성 규칙]")[1]

    from yt_monitor.generator import save_generation
    path = save_generation(g, tmp_path / "outputs", KST)
    assert path == tmp_path / "outputs" / "2026-09-01" / "내_채널.md"
    md = path.read_text(encoding="utf-8")
    assert "## 주제 후보" in md and "⭐ 모델 추천" in md and "📝 대본 작성" in md
    assert g.script_path.read_text(encoding="utf-8").splitlines()[0].startswith("여러분")
    assert save_generation(g, tmp_path / "outputs", KST) != path  # 같은 날 두 번째는 덮어쓰지 않음

    # 다른 주제로 대본 다시 쓰기 (GUI의 '선택한 주제로 대본 생성')
    gen.write_script(g, 0)
    assert g.selected == 0 and g.title == "3천원으로 일주일 도시락 끝"
    assert "대본" in build_generation_html(g)


# ---- 4) 하락 감지 → 자동 생성 → 알림 ----------------------------------------------------

def test_pipeline_generates_on_slowdown(ollama, tmp_path):
    url, _ = ollama
    cfg = ollama_config(tmp_path, url)
    fake = FakeYouTube(make_videos(40, 100), T0)
    notifier = FakeNotifier()
    r = run_check(cfg, service=fake, notifier=notifier, now=T0)[0]
    assert r.error is None and r.alerted and r.generation is not None
    g, err = notifier.generations[0]
    assert g is r.generation and err is None
    assert g.output_path.exists() and g.output_path.parent.parent == tmp_path / "outputs"
    with Database(cfg.db_path) as db:
        assert db.counts()["generations"] == 1
        assert db.last_generation(r.analysis.channel_id)["trigger"] == "auto"

    # 쿨다운 안에서는 다시 생성하지 않는다
    fake.now = T0 + timedelta(hours=6)
    r = run_check(cfg, service=fake, notifier=notifier, now=fake.now)[0]
    assert r.generation is None and len(notifier.generations) == 1


def test_pipeline_alerts_even_if_ollama_down(tmp_path):
    cfg = ollama_config(tmp_path, "http://127.0.0.1:9")
    notifier = FakeNotifier()
    r = run_check(cfg, service=FakeYouTube(make_videos(40, 100), T0), notifier=notifier, now=T0)[0]
    assert r.alerted and r.generation is None and "OllamaUnavailable" in r.generation_error
    assert notifier.generations[0] == (None, r.generation_error)


def test_no_generation_without_slowdown(ollama, tmp_path):
    url, handler = ollama
    cfg = ollama_config(tmp_path, url)
    r = run_check(cfg, service=FakeYouTube(make_videos(150, 100), T0),
                  notifier=FakeNotifier(), now=T0)[0]
    assert not r.analysis.slowdown and r.generation is None and handler.requests_log == []


def test_manual_generate_for_channel(ollama, tmp_path):
    url, _ = ollama
    cfg = ollama_config(tmp_path, url)
    run_check(cfg, service=FakeYouTube(make_videos(150, 100), T0), notifier=FakeNotifier(),
              generate=False, now=T0)
    statuses = []
    g = generate_for_channel(cfg, "UC_TEST", now=T0, on_status=statuses.append)
    assert g.trigger == "manual" and g.output_path.name == "테스트채널.md"
    assert "상위 성과 영상" in g.context and len(statuses) == 2
    with pytest.raises(GenerationError):
        generate_for_channel(cfg, "UC_EMPTY", now=T0)


def test_scheduler_interval_hours(tmp_path):
    raw = yaml.safe_load(write_config(tmp_path).read_text(encoding="utf-8"))
    raw["schedule"] = {"interval_hours": 3}
    cfg = load_config(save_config(raw, tmp_path / "c2.yaml"), load_env=False)
    from yt_monitor.scheduler import build_scheduler, describe_schedule

    scheduler, _ = build_scheduler(cfg, blocking=False, job=lambda: None)
    assert "3:00:00" in str(scheduler.get_jobs()[0].trigger)
    assert describe_schedule(cfg) == "3시간마다"


# ---- 영어 모드: 모델 머리말 · 한국어 줄 · 제목 반복 걸러내기 ------------------------------

QWEN_EN_OUTPUT = """Sure, here is the voice-over script for your YouTube Shorts video on 90s nostalgia memories:
90s 추억, 기억해?
90s Childhood, Do You Remember?
What do you remember from the 90s?
Back then, we wore flip flops and listened to grunge music.
The last one is wild—what's your favorite 90s memory?
"""


def test_tts_lines_english_drops_preamble_korean_and_title():
    from yt_monitor.generator import tts_lines

    lines = tts_lines(QWEN_EN_OUTPUT, "en", ["90s Childhood, Do You Remember?"])
    assert lines == ["What do you remember from the 90s?",
                     "Back then, we wore flip flops and listened to grunge music.",
                     "The last one is wild—what's your favorite 90s memory?"]
    # 한국어 모드는 한글 문장을 그대로 둔다
    assert "90s 추억, 기억해?" in tts_lines(QWEN_EN_OUTPUT, "ko")
    # 대본 중간에 제목과 같은 문장이 나오면 (훅으로 쓴 경우) 지우지 않는다
    assert tts_lines("Hook line!\nTitle Here\n", "en", ["Title Here"]) == ["Hook line!", "Title Here"]
    # 평범한 "Here's" 문장은 머리말이 아니다
    assert tts_lines("Here's the crazy part.\n", "en") == ["Here's the crazy part."]


def test_pick_title_prefers_english_in_english_mode():
    from yt_monitor.generator import pick_title

    t = {"topic": "90s nostalgia", "titles": ["90s 추억, 기억해?", "90s Kids Remember This?"]}
    assert pick_title(t, "en") == "90s Kids Remember This?"
    assert pick_title(t, "ko") == "90s 추억, 기억해?"
    assert pick_title({"topic": "90s nostalgia", "titles": ["90s 추억"]}, "en") == "90s nostalgia"
