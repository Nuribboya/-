"""영상을 더 눈에 띄게: 줌·색감 효과, 첫 화면 훅 문구, Pixabay, AI 이미지(ComfyUI)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from fake_video_services import (FakeEdgeTTS, make_test_clip, start_fake_comfy,  # noqa: E402
                                 start_fake_pexels, start_fake_pixabay)
from yt_monitor.config import Config, read_raw, save_config  # noqa: E402
from yt_monitor.ollama_client import OllamaClient  # noqa: E402
from yt_monitor.video import compose  # noqa: E402
from yt_monitor.video.comfyui import ComfyClient, ComfyError, build_workflow  # noqa: E402
from yt_monitor.video.ffmpeg import FFmpegRunner, check_ffmpeg, probe  # noqa: E402
from yt_monitor.video.pexels import PexelsAuthError, PexelsClient  # noqa: E402
from yt_monitor.video.pipeline import VideoPipeline  # noqa: E402
from yt_monitor.video.pixabay import PixabayClient  # noqa: E402
from yt_monitor.video.subtitles import Cue, hook_display, to_ass  # noqa: E402

FF = check_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(not FF.ok, reason="ffmpeg 없음")


@pytest.fixture(scope="module")
def png(tmp_path_factory):
    if not FF.ok:
        pytest.skip("ffmpeg 없음")
    path = tmp_path_factory.mktemp("img") / "ai.png"
    import subprocess

    subprocess.run([FF.path, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "mandelbrot=s=384x672",
                    "-frames:v", "1", str(path)], check=True)
    return path.read_bytes()


# ---- 효과 ---------------------------------------------------------------------------

def test_shot_filter_zoom_and_color():
    comp = compose.Composer.__new__(compose.Composer)
    comp.w, comp.h, comp.fps, comp.zoom, comp.contrast, comp.saturation = 1080, 1920, 30, 0.1, 1.1, 1.3
    vf = comp.shot_filter(compose.Shot(60, Path("a.mp4")))
    assert "setpts=PTS-STARTPTS" in vf and "(1+0.1*t/2.000)" in vf and "eval=frame" in vf
    assert "eq=contrast=1.1:saturation=1.3" in vf
    assert "(1+0.1*(1-t/2.000))" in comp.shot_filter(compose.Shot(60, Path("a.mp4"), zoom_out=True))
    plain = comp.shot_filter(compose.Shot(60, None))                 # 단색 배경은 효과 없음
    assert "eval=frame" not in plain and "eq=" not in plain
    comp.zoom = 0
    assert "eval=frame" not in comp.shot_filter(compose.Shot(60, Path("a.mp4")))
    assert compose.Shot(1, Path("x.PNG")).is_image and not compose.Shot(1, Path("x.mp4")).is_image


@needs_ffmpeg
def test_image_and_zoomed_video_shots_render(tmp_path, png):
    img = tmp_path / "ai.png"
    img.write_bytes(png)
    clip = tmp_path / "c.mp4"
    make_test_clip(FF.path, clip, size="320x180", seconds=1)
    comp = compose.Composer(FFmpegRunner(FF.path, tmp_path / "logs"), tmp_path / "w",
                            {"width": 180, "height": 320, "fps": 15, "preset": "ultrafast", "crf": 30,
                             "zoom": 0.1})
    for shot, name in ((compose.Shot(30, img), "img.mp4"), (compose.Shot(30, clip, zoom_out=True), "vid.mp4")):
        out = comp.prepare_shot(shot, tmp_path / name)
        info = probe(FF.path, out)
        assert (info.width, info.height) == (180, 320) and info.duration == pytest.approx(2.0, abs=0.1)
    log = next((tmp_path / "logs").glob("*img*")).read_text(encoding="utf-8")
    assert "-loop 1" in log


# ---- 훅 문구 ------------------------------------------------------------------------

def test_hook_in_ass():
    assert hook_display("  You've been doing this WRONG. ") == "YOU'VE BEEN DOING THIS WRONG"
    assert hook_display("이거 모르면 손해.") == "이거 모르면 손해"
    ass = to_ass([Cue(0, 1, "hi")], width=1080, height=1920, font="Arial Black",
                 hook=Cue(0, 2.5, "Stop scrolling"), hook_font_size=110)
    style = next(line for line in ass.splitlines() if line.startswith("Style: Hook"))
    f = style.split(",")
    assert f[1] == "Arial Black" and f[2] == "110" and f[3] == "&H0000F0FF"   # 노란 글씨
    assert f[15] == "1" and f[18] == "8"                                      # 테두리, 위쪽 가운데
    hook_line = next(line for line in ass.splitlines() if ",Hook," in line)
    assert hook_line.startswith("Dialogue: 1,0:00:00.00,0:00:02.50,Hook") and hook_line.endswith("STOP SCROLLING")
    assert "\\fad(" in hook_line and "\\t(" in hook_line
    assert ",Hook," not in to_ass([Cue(0, 1, "hi")])                          # 훅 없으면 안 들어감


# ---- 설정 업그레이드 ------------------------------------------------------------------

def test_config_upgrade_speeds_up_cuts_only_if_default(tmp_path):
    for old_value, expected in ((4.0, 2.5), (3.0, 3.0)):
        raw = read_raw(tmp_path / "none.yaml")
        del raw["config_version"]
        raw["video"]["clip_max_seconds"] = old_value
        raw["channels"] = [{"handle": "@x"}]
        path = save_config(raw, tmp_path / f"c{old_value}.yaml")
        new = read_raw(path)
        assert new["video"]["clip_max_seconds"] == expected and new["config_version"] == 2
        assert new["video"]["zoom"] == 0.08 and new["ai_images"]["enabled"] is False


# ---- Pixabay ------------------------------------------------------------------------

def test_pixabay_search_download_and_key_error(tmp_path):
    server, url, handler = start_fake_pixabay({"a.mp4": b"A", "b.mp4": b"B"})
    try:
        client = PixabayClient("good", api_url=url, target_size=(180, 320))
        clips = client.search("storm clouds dramatic")
        assert [c.source for c in clips] == ["pixabay", "pixabay"]
        assert clips[0].height > clips[0].width                   # 세로 영상이 앞으로
        assert clips[0].credit.startswith("pix1 / Pixabay — https://pixabay.com/videos/")
        path = client.download(clips[0], tmp_path)
        assert path.name.startswith("pixabay_") and path.read_bytes() == b"B"
        with pytest.raises(PexelsAuthError, match="Pixabay API 키"):
            PixabayClient("bad", api_url=url).search("x")
    finally:
        server.shutdown()


# ---- ComfyUI ------------------------------------------------------------------------

def test_comfy_status_and_generate(tmp_path):
    server, url, handler = start_fake_comfy(b"PNGDATA", new_combo_format=True)
    try:
        client = ComfyClient(url, style="cinematic", negative="text", width=512, height=896, steps=6, cfg=2,
                             poll_interval=0.01)
        st = client.status()
        assert st.ok and st.checkpoint == "juggernautXL_lightning.safetensors"
        out = client.generate("a giant shark", tmp_path / "x" / "a.png", seed=7)
        assert out.read_bytes() == b"PNGDATA"
        wf = handler.prompts[0]
        assert wf["4"]["inputs"]["ckpt_name"] == "juggernautXL_lightning.safetensors"
        assert wf["5"]["inputs"] == {"width": 512, "height": 896, "batch_size": 1}
        assert wf["6"]["inputs"]["text"] == "a giant shark, cinematic" and wf["7"]["inputs"]["text"] == "text"
        assert wf["3"]["inputs"]["seed"] == 7 and wf["3"]["inputs"]["steps"] == 6
        assert ComfyClient(url, checkpoint="없는모델").status().ok is False
    finally:
        server.shutdown()
    assert "comfy.org" in ComfyClient("http://127.0.0.1:9").status().message


def test_comfy_error_is_reported(tmp_path):
    server, url, _ = start_fake_comfy(b"x", fail=True)
    try:
        with pytest.raises(ComfyError, match="CUDA out of memory"):
            ComfyClient(url, poll_interval=0.01).generate("x", tmp_path / "a.png")
    finally:
        server.shutdown()


def test_build_workflow_links_nodes():
    wf = build_workflow("p", "n", checkpoint="m.safetensors", width=768, height=1344, steps=6, cfg=2.0,
                        sampler="dpmpp_sde", scheduler="karras", seed=1)
    assert wf["3"]["inputs"]["model"] == ["4", 0] and wf["8"]["inputs"]["vae"] == ["4", 2]
    assert wf["9"]["class_type"] == "SaveImage"


# ---- 전체 파이프라인 ------------------------------------------------------------------

def make_cfg(tmp_path, **ai) -> Config:
    raw = read_raw(tmp_path / "config.yaml")
    raw["video"].update(width=180, height=320, fps=15, crf=30, preset="ultrafast", clip_max_seconds=1.5,
                        subtitle_font_size=20, subtitle_margin_bottom=40, hook_font_size=24)
    raw["ai_images"].update(enabled=True, **ai)
    return Config(raw=raw, base_dir=tmp_path, path=tmp_path / "config.yaml")


@needs_ffmpeg
def test_pipeline_mixes_pexels_pixabay_and_ai_images(tmp_path, png):
    clips = {n: make_test_clip(FF.path, tmp_path / n, p, seconds=1)
             for n, p in (("a.mp4", "testsrc2"), ("b.mp4", "smptebars"))}
    pex, pex_url, _ = start_fake_pexels(clips)
    pix, pix_url, _ = start_fake_pixabay(clips)
    comfy, comfy_url, comfy_h = start_fake_comfy(png)
    ol, ol_url, ol_h = start_fake_ollama()
    try:
        cfg = make_cfg(tmp_path, host=comfy_url)
        pipe = VideoPipeline(cfg, pexels=PexelsClient("k", api_url=pex_url, target_size=(180, 320)),
                             pixabay=PixabayClient("good", api_url=pix_url, target_size=(180, 320)),
                             tts=FakeEdgeTTS(retry_delay=0), ollama=OllamaClient(ol_url, "qwen2.5:7b"),
                             comfy=ComfyClient(comfy_url, poll_interval=0.01))
        pipe._ollama_used = pipe._ollama               # 테스트용 주입 클라이언트도 내리기 대상
        statuses = []
        script = ("Gas station snacks are secretly ranked. The first one costs way more than it should. "
                  "The honey bun tastes like childhood. Comment your favorite below.")
        res = pipe.run(script, "Snacks Ranked", on_status=statuses.append)
        assert res.video_path.exists() and not res.warnings, res.warnings

        debug = json.loads((res.work_dir / "scenes.json").read_text(encoding="utf-8"))
        first = debug["shots"][0]["source"]
        assert first.endswith("scene_001.png")                       # 첫 컷(훅)은 AI 이미지
        assert any("pixabay_" in (s["source"] or "") for s in debug["shots"])
        assert any("pexels_" in (s["source"] or "") for s in debug["shots"])
        assert debug["scenes"][0]["image_prompt"].startswith("neon convenience store")
        assert "neon convenience store" in comfy_h.prompts[0]["6"]["inputs"]["text"]
        assert ol_h.unloads == ["qwen2.5:7b"] and comfy_h.freed      # 그래픽카드 메모리 양보
        credits = res.credits_path.read_text(encoding="utf-8")
        assert credits.startswith("Stock footage: Pexels (pexels.com), Pixabay (pixabay.com)")
        ass = (res.work_dir / "subtitles.ass").read_text(encoding="utf-8")
        assert ",Hook,,0,0,0,," in ass and "SNACKS RANKED" in ass
        assert any("훅 문구" in m for m in statuses)
    finally:
        for srv in (pex, pix, comfy, ol):
            srv.shutdown()


@needs_ffmpeg
def test_pipeline_continues_when_comfy_is_down(tmp_path):
    cfg = make_cfg(tmp_path, host="http://127.0.0.1:9")
    from yt_monitor.video.tts import PlaceholderTTS

    res = VideoPipeline(cfg, tts=PlaceholderTTS(), offline=True,
                        comfy=ComfyClient("http://127.0.0.1:9")).run("Short script here. Another line.", "t",
                                                                    hook_text="")
    assert res.video_path.exists()
    assert any("AI 이미지를 건너뜁니다" in w for w in res.warnings)
    assert ",Hook," not in (res.work_dir / "subtitles.ass").read_text(encoding="utf-8")   # hook_text="" → 없음



# ---- 분위기별 음성 자동 선택 -----------------------------------------------------------

def test_voice_for_mood_presets_and_overrides():
    from yt_monitor.video.tts import MOOD_VOICES, MOODS, VOICES, voice_for_mood

    assert set(MOOD_VOICES["en"]) == set(MOODS) == set(MOOD_VOICES["ko"])
    for lang in ("en", "ko"):
        for preset in MOOD_VOICES[lang].values():
            assert preset["voice"] in VOICES                      # GUI 목록에 있는 음성만 사용
            assert preset["voice"].startswith("en-" if lang == "en" else "ko-")
    assert voice_for_mood("mysterious", "en")["pitch"].startswith("-")
    assert voice_for_mood("없는분위기", "en")["mood"] == "energetic"
    over = voice_for_mood("calm", "en", {"en": {"calm": {"voice": "en-GB-RyanNeural"}}})
    assert over["voice"] == "en-GB-RyanNeural" and over["rate"] == MOOD_VOICES["en"]["calm"]["rate"]


def test_guess_mood_without_ollama():
    from yt_monitor.video.scenes import guess_mood

    assert guess_mood("The haunted lighthouse nobody explains") == "mysterious"
    assert guess_mood("This cute dog did the funniest thing") == "playful"
    assert guess_mood("5 snacks ranked") == "energetic"


@needs_ffmpeg
@pytest.mark.parametrize("chosen, expect_voice, expect_rate", [
    (None, "en-US-ChristopherNeural", "+2%"),          # 가짜 Ollama가 mood=dramatic → 긴장감 있는 목소리
    ("en-US-JennyNeural", "en-US-JennyNeural", None),   # 직접 고르면 그 음성
])
def test_pipeline_picks_voice_by_mood(tmp_path, monkeypatch, chosen, expect_voice, expect_rate):
    from yt_monitor.video import pipeline as pl

    made = []

    class RecordingTTS(FakeEdgeTTS):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.retry_delay = 0
            made.append(self)

    monkeypatch.setattr(pl, "EdgeTTS", RecordingTTS)
    ol, ol_url, _ = start_fake_ollama()
    try:
        cfg = make_cfg(tmp_path)
        cfg.raw["ai_images"]["enabled"] = False
        statuses = []
        res = VideoPipeline(cfg, ollama=OllamaClient(ol_url, "qwen2.5:7b")).run(
            "Nobody knows why this happened. The truth is shocking.", "t", voice=chosen, hook_text="",
            on_status=statuses.append)
        assert made[0].voice == expect_voice and res.voice == expect_voice and res.mood == "dramatic"
        if expect_rate:
            assert made[0].rate == expect_rate and made[0].pitch == "-3Hz"
            assert any("분위기: dramatic" in m for m in statuses)
        else:
            assert any("직접 선택" in m for m in statuses)
    finally:
        ol.shutdown()
