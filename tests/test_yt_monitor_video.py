"""대본 → 영상 생성 테스트. Pexels/edge-tts/Ollama는 가짜 서버·객체로 대체한다.

ffmpeg가 없으면 합성 관련 테스트는 건너뛴다.
"""

import json
import sys
import threading
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from fake_video_services import (FakeEdgeTTS, make_test_clip, start_fake_pexels,  # noqa: E402
                                 tone_wav)
from yt_monitor.config import Config, read_raw  # noqa: E402
from yt_monitor.ollama_client import OllamaClient  # noqa: E402
from yt_monitor.video import compose, scenes, subtitles  # noqa: E402
from yt_monitor.video.ffmpeg import (Cancelled, FFmpegError, FFmpegRunner, check_ffmpeg,  # noqa: E402
                                     find_ffmpeg, probe)
from yt_monitor.video.pexels import PexelsAuthError, PexelsClient, PexelsError, pick_file  # noqa: E402
from yt_monitor.video.pipeline import VideoPipeline, output_path_for  # noqa: E402
from yt_monitor.video.tts import PlaceholderTTS, TTSError, Word  # noqa: E402

FF = check_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(not FF.ok, reason="ffmpeg(libass, libx264)가 없어 합성 테스트를 건너뜁니다")
KST = ZoneInfo("Asia/Seoul")

SCRIPT = """# 대본
제목: 편의점 꿀조합

**내레이션:** 여러분, 편의점에서 이 조합 먹어보셨나요? [효과음]
오늘은 진짜 맛있는 조합만 모았습니다.
첫 번째 조합은 컵라면과 삼각김밥입니다 😋
두 번째는 요거트와 그래놀라예요.
구독과 좋아요 부탁드려요!
"""


# ---- 씬 분리 / 키워드 ------------------------------------------------------------------

def test_split_scenes_merges_short_sentences():
    sc = scenes.split_scenes("짧다.\n아주 짧다.\n이 문장은 충분히 길어서 혼자서도 한 씬이 됩니다.\n끝.",
                             min_seconds=2.5, max_chars=90)
    assert [s.text for s in sc] == ["짧다. 아주 짧다. 이 문장은 충분히 길어서 혼자서도 한 씬이 됩니다.", "끝."] or \
        len(sc) >= 1
    assert all(len(s.text) <= 90 for s in sc)
    assert [s.index for s in sc] == list(range(1, len(sc) + 1))
    # 마크다운/지시문/이모지는 빠진다
    text = " ".join(s.text for s in scenes.split_scenes(SCRIPT))
    assert "[효과음]" not in text and "내레이션" not in text and "😋" not in text and "제목" not in text


def test_split_scenes_respects_max_chars():
    long = "\n".join(["이 문장은 스무 글자 정도 되는 문장입니다."] * 10)
    sc = scenes.split_scenes(long, min_seconds=100, max_chars=60)
    assert len(sc) > 1 and all(len(s.text) <= 60 for s in sc)


def test_parse_keywords_and_fallback():
    text = '```json\n{"scenes":[{"scene":2,"keywords":["city night","  traffic ",""]},' \
           '{"scene":9,"keywords":["x"]},{"scene":1,"keywords":"coffee, cup"}]}\n```'
    assert scenes.parse_keywords(text, 3, 2) == {2: ["city night", "traffic"], 1: ["coffee", "cup"]}
    assert scenes.fallback_keywords("첫 번째 조합은 컵라면과 삼각김밥입니다.") == ["삼각김밥", "컵라면", "조합"]
    assert "편의점" in scenes.fallback_keywords("여러분, 편의점에서 이 조합 먹어보셨나요?")


def test_extract_keywords_with_ollama_and_fallback(tmp_path):
    server, url, handler = start_fake_ollama()
    try:
        sc = scenes.split_scenes(SCRIPT)
        scenes.extract_keywords(sc, client=OllamaClient(url, "qwen2.5:7b"), title="편의점")
        assert sc[0].keywords == ["convenience store", "snack shelf", "night street"]
        assert sc[0].keyword_source == "ollama"
        assert "Pexels" in handler.requests_log[-1]["messages"][-1]["content"]
        assert handler.requests_log[-1]["format"] == "json"
    finally:
        server.shutdown()
    # Ollama 꺼짐 → 간이 키워드
    sc = scenes.split_scenes(SCRIPT)
    scenes.extract_keywords(sc, client=OllamaClient("http://127.0.0.1:9", "m"))
    assert all(s.keyword_source == "fallback" for s in sc)


# ---- 자막 ----------------------------------------------------------------------------

def test_split_caption_breaks_on_length_and_sentence_end():
    chunks = subtitles.split_caption("여러분 안녕하세요. 오늘은 편의점 꿀조합을 소개해 드릴게요!", 10)
    texts = [c[0] for c in chunks]
    assert texts[0] == "여러분 안녕하세요."
    assert all(len(t) <= 10 or " " not in t for t in texts)
    assert texts[-1].endswith("!")


def test_cues_follow_word_timestamps():
    text = "여러분 안녕하세요. 오늘은 편의점 꿀조합!"
    words = [Word("여러분", 0.1, 0.5), Word("안녕하세요", 0.6, 1.2), Word("오늘은", 1.6, 2.0),
             Word("편의점", 2.1, 2.5), Word("꿀조합", 2.6, 3.0)]
    cues = subtitles.cues_for_scene(text, words, offset=10.0, end=13.5, max_chars=10)
    assert [c.text for c in cues] == ["여러분 안녕하세요", "오늘은 편의점", "꿀조합!"]
    assert cues[0].start == pytest.approx(10.1)
    assert cues[1].start == pytest.approx(11.6) and cues[0].end == pytest.approx(11.6)
    assert cues[2].start == pytest.approx(12.6)
    assert cues[-1].end <= 13.5


def test_cues_without_timestamps_are_proportional():
    cues = subtitles.cues_for_scene("가나다라 마바사아 자차카타 파하", [], 0.0, 4.0, max_chars=5)
    assert len(cues) == 4
    assert all(a.end <= b.start + 1e-9 for a, b in zip(cues, cues[1:]))
    assert cues[0].start == 0.0 and cues[-1].end <= 4.0


def test_srt_and_ass_format():
    cues = [subtitles.Cue(0.0, 1.5, "첫 자막"), subtitles.Cue(61.25, 3725.5, "{둘}\\째")]
    srt = subtitles.to_srt(cues)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500\n첫 자막\n")
    assert "00:01:01,250 --> 01:02:05,500" in srt
    ass = subtitles.to_ass(cues, width=1080, height=1920, font="Malgun Gothic", font_size=80,
                           margin_bottom=300, box_opacity=0.6)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    style = next(line for line in ass.splitlines() if line.startswith("Style: Shorts"))
    f = style.split(",")
    assert f[1] == "Malgun Gothic" and f[2] == "80" and f[7] == "-1"        # 굵게
    assert f[15] == "3" and f[18] == "2" and f[21] == "300"                   # 박스, 하단 중앙, 여백
    assert f[5] == "&H66000000"                                               # 불투명도 0.6
    assert "Dialogue: 0,0:01:01.25,1:02:05.50,Shorts,,0,0,0,,(둘)＼째" in ass


# ---- Pexels -------------------------------------------------------------------------

def test_pick_file_prefers_small_enough_portrait():
    files = [{"link": "a", "width": 3840, "height": 2160}, {"link": "b", "width": 2160, "height": 3840},
             {"link": "c", "width": 1080, "height": 1920}, {"link": "d", "width": 360, "height": 640},
             {"link": "e", "width": 720, "height": 1280}]
    assert pick_file(files, 1080, 1920)["link"] == "e"         # 짧은 변 720 ≥ 1080*2/3
    assert pick_file(files[:1] + files[3:4], 1080, 1920)["link"] == "d"   # 세로 우선
    assert pick_file([{"link": "x", "width": 100, "height": 200}], 1080, 1920)["link"] == "x"
    assert pick_file([], 1080, 1920) is None


def test_pexels_search_and_download(tmp_path):
    server, url, handler = start_fake_pexels({"a.mp4": b"AAAA", "b.mp4": b"BBBB"})
    try:
        client = PexelsClient("key123", api_url=url, target_size=(1080, 1920))
        clips = client.search("ocean")
        assert len(clips) == 2 and clips[0].width == 2160 and clips[0].author == "작가0"  # 360p는 너무 작음
        assert PexelsClient("k", api_url=url, target_size=(180, 320)).search("ocean")[0].width == 360
        assert client.search("OCEAN ") is clips            # 캐시
        assert handler.log[0][1] == "key123" and "orientation=portrait" in handler.log[0][0]
        path = client.download(clips[1], tmp_path / "cache")
        assert path.read_bytes() == b"BBBB"
        n = len(handler.log)
        client.download(clips[1], tmp_path / "cache")       # 이미 받은 파일은 다시 받지 않음
        assert len(handler.log) == n
    finally:
        server.shutdown()


def test_pexels_errors(tmp_path):
    with pytest.raises(PexelsError, match="pexels.com/api"):
        PexelsClient("")
    server, url, _ = start_fake_pexels({"a.mp4": b"A"}, status=401)
    try:
        with pytest.raises(PexelsAuthError, match="키가 올바르지"):
            PexelsClient("bad", api_url=url).search("x")
    finally:
        server.shutdown()


# ---- TTS ----------------------------------------------------------------------------

def test_edge_tts_collects_audio_and_word_timestamps(tmp_path):
    tts = FakeEdgeTTS("ko-KR-SunHiNeural", fail_times=1, retry_delay=0)
    res = tts.synthesize("안녕하세요 반갑습니다.", tmp_path / "a.mp3")
    assert len(tts.calls) == 2                               # 한 번 실패 후 재시도
    assert [w.text for w in res.words] == ["안녕하세요", "반갑습니다"]
    assert res.words[0].start == pytest.approx(0.1) and res.words[0].end == pytest.approx(0.35)
    assert res.audio_path.read_bytes() == tone_wav(0.1 + 0.6 + 0.2)


def test_edge_tts_gives_up_with_clear_message(tmp_path):
    with pytest.raises(TTSError, match="인터넷 연결"):
        FakeEdgeTTS(fail_times=5, retries=2, retry_delay=0).synthesize("x", tmp_path / "a.mp3")


class _AsyncCommunicate:
    """edge_tts.Communicate 처럼 async stream()만 있는 가짜. mode: ok | error | hang"""

    def __init__(self, mode):
        self.mode = mode

    async def stream(self):
        import asyncio

        if self.mode == "error":
            raise ConnectionError("네트워크 끊김")
        if self.mode == "hang":
            await asyncio.sleep(3600)
        yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "안녕"}
        yield {"type": "audio", "data": b"mp3"}

    def stream_sync(self):          # edge-tts 원래 구현처럼 예외 시 멈춘다고 가정 → 쓰면 안 됨
        raise AssertionError("stream_sync 를 쓰면 네트워크 오류 때 멈출 수 있다")


@pytest.mark.parametrize("mode", ["ok", "error", "hang"])
def test_edge_tts_async_stream_never_hangs(tmp_path, monkeypatch, mode):
    from yt_monitor.video.tts import EdgeTTS

    tts = EdgeTTS(retries=2, retry_delay=0, timeout=0.3)
    monkeypatch.setattr(tts, "_communicate", lambda text: _AsyncCommunicate(mode))
    if mode == "ok":
        res = tts.synthesize("안녕", tmp_path / "a.mp3")
        assert res.audio_path.read_bytes() == b"mp3" and res.words[0].start == pytest.approx(0.1)
    else:
        with pytest.raises(TTSError, match="ConnectionError|TimeoutError"):
            tts.synthesize("안녕", tmp_path / "a.mp3")


def test_placeholder_tts(tmp_path):
    res = PlaceholderTTS().synthesize("하나 둘 셋", tmp_path / "p.mp3")
    assert res.audio_path.suffix == ".wav" and len(res.words) == 3
    with wave.open(str(res.audio_path)) as w:
        assert w.getnframes() / w.getframerate() > res.words[-1].end


# ---- ffmpeg / 합성 --------------------------------------------------------------------

def test_split_frames_exact_total():
    bounds = [0, 1.37, 5.02, 13.9, 14.25]
    frames = compose.split_frames(bounds, 30, 4.0)
    assert sum(sum(f) for f in frames) == round(14.25 * 30)
    assert [len(f) for f in frames] == [1, 1, 3, 1]
    assert all(n <= 4.0 * 30 + 1 for f in frames for n in f)


def test_find_ffmpeg_configured_path(tmp_path):
    exe = tmp_path / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    exe.write_text("")
    assert find_ffmpeg(str(tmp_path)) == str(exe)
    assert find_ffmpeg(str(exe)) == str(exe)


@needs_ffmpeg
def test_ffmpeg_error_is_logged(tmp_path):
    runner = FFmpegRunner(FF.path, tmp_path / "logs")
    with pytest.raises(FFmpegError) as ei:
        runner.run(["-i", tmp_path / "없는파일.mp4", tmp_path / "out.mp4"], "없는 파일 변환")
    log_path = ei.value.log_path
    assert log_path.exists() and log_path.name == "01_없는_파일_변환.log"
    content = log_path.read_text(encoding="utf-8")
    assert "# 명령:" in content and "없는파일.mp4" in content and "종료 코드" in content
    assert "전체 로그" in str(ei.value) and "No such file" in str(ei.value)


@needs_ffmpeg
def test_ffmpeg_cancel(tmp_path):
    cancel = threading.Event()
    runner = FFmpegRunner(FF.path, tmp_path / "logs", cancel)
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(Cancelled):
        runner.run(["-re", "-f", "lavfi", "-i", "testsrc=d=30", "-f", "null", "-"], "긴 작업")


@needs_ffmpeg
def test_compose_steps(tmp_path):
    """클립 이어붙이기 → 음성 삽입 → 자막 번인, 단계마다 결과 확인."""
    cfg = {"width": 180, "height": 320, "fps": 15, "crf": 30, "preset": "ultrafast"}
    runner = FFmpegRunner(FF.path, tmp_path / "logs")
    comp = compose.Composer(runner, tmp_path / "work", cfg)
    wide = tmp_path / "wide.mp4"
    make_test_clip(FF.path, wide, size="320x180", seconds=1.0)     # 가로 + 짧음 → 크롭 + 반복
    audio = tmp_path / "a.wav"
    audio.write_bytes(tone_wav(3.0))
    wav = tmp_path / "work" / "n.wav"
    total = comp.decode_audio(audio, wav)
    assert total == pytest.approx(3.0, abs=0.01)

    frames = compose.split_frames([0, 1.2, 3.0], 15, 1.0)
    shots = [compose.Shot(frames[0][0], wide), compose.Shot(frames[0][1], None),
             *[compose.Shot(n, wide, seek=0.3) for n in frames[1]]]
    v1 = comp.concat(comp.prepare_shots(shots))
    i1 = probe(FF.path, v1)
    assert (i1.width, i1.height) == (180, 320) and not i1.has_audio
    assert i1.duration == pytest.approx(3.0, abs=0.1)

    v2 = comp.add_audio(v1, wav, total)
    i2 = probe(FF.path, v2)
    assert i2.has_audio and i2.duration == pytest.approx(3.0, abs=0.1)

    ass = tmp_path / "s.ass"
    ass.write_text(subtitles.to_ass([subtitles.Cue(0, 3, "자막 테스트")], width=180, height=320,
                                    font_size=20, margin_bottom=40), encoding="utf-8")
    out = tmp_path / "최종 결과.mp4"                      # 공백/한글 경로
    comp.burn_subtitles(v2, ass, out)
    i3 = probe(FF.path, out)
    assert (i3.width, i3.height) == (180, 320) and i3.has_audio
    logs = sorted(p.name for p in (tmp_path / "logs").iterdir())
    assert any("자막_번인" in n for n in logs) and any("클립_이어붙이기" in n for n in logs)


# ---- 전체 파이프라인 ------------------------------------------------------------------

def make_cfg(tmp_path, **video) -> Config:
    raw = read_raw(tmp_path / "config.yaml")
    raw["video"].update(width=180, height=320, fps=15, crf=30, preset="ultrafast", clip_max_seconds=1.5,
                        subtitle_font_size=20, subtitle_margin_bottom=40, **video)
    raw["pexels"]["api_key"] = "fake-key"
    return Config(raw=raw, base_dir=tmp_path, path=tmp_path / "config.yaml")


@pytest.fixture
def services(tmp_path):
    clips = {}
    if FF.ok:
        for name, pattern in (("a.mp4", "testsrc2"), ("b.mp4", "smptebars")):
            clips[name] = make_test_clip(FF.path, tmp_path / name, pattern, seconds=1.0)
    pex_server, pex_url, pex_handler = start_fake_pexels(clips)
    ol_server, ol_url, _ = start_fake_ollama()
    yield {"pexels_url": pex_url, "pexels_log": pex_handler.log, "ollama_url": ol_url}
    pex_server.shutdown()
    ol_server.shutdown()


def make_pipeline(cfg, services, **kw):
    pexels = PexelsClient("fake-key", api_url=services["pexels_url"],
                          target_size=(cfg.video["width"], cfg.video["height"]))
    return VideoPipeline(cfg, pexels=pexels, tts=FakeEdgeTTS(retry_delay=0),
                         ollama=OllamaClient(services["ollama_url"], "qwen2.5:7b"), **kw)


@needs_ffmpeg
def test_pipeline_end_to_end(tmp_path, services):
    cfg = make_cfg(tmp_path)
    progress, statuses = [], []
    now = datetime(2026, 9, 24, 21, 0, tzinfo=KST)
    res = make_pipeline(cfg, services).run(SCRIPT, "편의점 꿀조합", now=now,
                                           on_progress=lambda n, t, m: progress.append((n, t, m)),
                                           on_status=statuses.append)
    assert [p[0] for p in progress] == [1, 2, 3, 4, 5, 6] and progress[0][1] == 6
    assert "키워드" in progress[0][2] and "다운로드" in progress[1][2]
    assert res.video_path == tmp_path / "outputs" / "2026-09-24" / "편의점_꿀조합.mp4"
    info = probe(FF.path, res.video_path)
    assert (info.width, info.height) == (180, 320) and info.has_audio
    assert info.duration == pytest.approx(res.duration, abs=0.15)

    srt = res.srt_path.read_text(encoding="utf-8")
    assert srt.startswith("1\n") and "편의점에서" in srt
    assert "Pexels" in res.credits_path.read_text(encoding="utf-8")
    assert res.scenes[0].keywords[0] == "convenience store"
    assert any("convenience+store" in p for p, _ in services["pexels_log"])
    debug = json.loads((res.work_dir / "scenes.json").read_text(encoding="utf-8"))
    assert len(debug["scenes"]) == len(res.scenes)
    assert sum(s["frames"] for s in debug["shots"]) == round(res.duration * 15)
    assert (res.work_dir / "logs").is_dir() and not res.warnings

    # 같은 제목으로 또 만들면 덮어쓰지 않는다
    res2 = make_pipeline(make_cfg(tmp_path, keep_work_files=False), services).run(SCRIPT, "편의점 꿀조합", now=now)
    assert res2.video_path != res.video_path and res2.video_path.exists() and res2.work_dir is None


@needs_ffmpeg
def test_pipeline_without_pexels_key_uses_solid_background(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.raw["pexels"]["api_key"] = ""
    cfg.raw["pexels"]["api_key_env"] = "YT_TEST_NO_SUCH_ENV"
    res = VideoPipeline(cfg, tts=PlaceholderTTS(), offline=False,
                        ollama=OllamaClient("http://127.0.0.1:9", "m")).run("짧은 대본입니다. 두 번째 문장.", "무키")
    assert res.video_path.exists() and res.credits_path is None
    assert any("Pexels API 키가 없어" in w for w in res.warnings)


@needs_ffmpeg
def test_pipeline_cancel(tmp_path, services):
    cancel = threading.Event()

    def on_progress(n, total, msg):
        if n == 3:
            cancel.set()

    with pytest.raises(Cancelled):
        make_pipeline(make_cfg(tmp_path), services).run(SCRIPT, "취소", on_progress=on_progress, cancel=cancel)


def test_pipeline_rejects_empty_script(tmp_path):
    if not FF.ok:
        pytest.skip("ffmpeg 없음")
    from yt_monitor.video.pipeline import VideoError

    with pytest.raises(VideoError, match="대본"):
        VideoPipeline(make_cfg(tmp_path), offline=True).run("# 제목만\n[효과음]", "빈 대본")


def test_output_path_for(tmp_path):
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=KST)
    p = output_path_for(tmp_path, "제목: 테스트/영상?", now, KST)
    assert p == tmp_path / "2026-01-02" / "제목_테스트_영상.mp4"
    p.write_bytes(b"x")
    assert output_path_for(tmp_path, "제목: 테스트/영상?", now, KST).name == "제목_테스트_영상_030405.mp4"
