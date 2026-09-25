"""GUI 테스트. tkinter와 화면(DISPLAY)이 있을 때만 실행된다 (리눅스는 xvfb-run pytest ...)."""

import os
import sys
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from test_yt_monitor import T0, FakeNotifier, FakeYouTube, make_videos  # noqa: E402
from yt_monitor import gui  # noqa: E402
from yt_monitor.config import load_config, read_raw, save_config  # noqa: E402
from yt_monitor.pipeline import run_check  # noqa: E402

PROMPTS = Path(__file__).resolve().parent.parent / "yt_monitor" / "prompts"


# ---- 순수 함수 ---------------------------------------------------------------------

def test_sparkline():
    assert gui.sparkline([]) == ""
    assert gui.sparkline([5, 5]) == "▄▄"
    assert gui.sparkline([0, 50, 100]) == "▁▄█"


def test_parse_channel_lines():
    text = """@mychannel | 메인
https://www.youtube.com/@sub_ch
https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv
UCabcdefghijklmnopqrstuv | 두번째
handle_only
# 주석"""
    assert gui.parse_channel_lines(text) == [
        {"handle": "@mychannel", "name": "메인"},
        {"handle": "@sub_ch"},
        {"id": "UCabcdefghijklmnopqrstuv"},
        {"id": "UCabcdefghijklmnopqrstuv", "name": "두번째"},
        {"handle": "@handle_only"},
    ]
    assert gui.channel_lines([{"handle": "@a", "name": "A"}, {"id": "UC1"}]) == "@a | A\nUC1"


# ---- 실제 창 -----------------------------------------------------------------------

@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("화면(DISPLAY)이 없어 GUI 테스트를 건너뜁니다.")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def dialogs(monkeypatch):
    calls = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(gui.messagebox, name, lambda *a, _n=name, **k: calls.append((_n, a)))
    return calls


def pump(root, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError("시간 초과")


def make_config(tmp_path, url, model="qwen2.5:7b"):
    raw = read_raw(tmp_path / "config.yaml")
    raw["channels"] = [{"handle": "@test", "name": "테스트채널"}]
    raw["youtube"]["api_key"] = "fake"
    raw["telegram"]["enabled"] = False
    raw["ollama"].update(host=url, model=model)
    return save_config(raw, tmp_path / "config.yaml")


def test_app_flow(root, dialogs, tmp_path):
    server, url, _ = start_fake_ollama()
    try:
        path = make_config(tmp_path, url)
        cfg = load_config(path, load_env=False)
        run_check(cfg, service=FakeYouTube(make_videos(40, 100), T0), notifier=FakeNotifier(),
                  generate=False, now=T0)

        app = gui.App(root, path, check_ollama_on_start=False)
        assert (tmp_path / "prompts" / "script_gen.txt").exists()   # 프롬프트 기본본 복사
        pump(root, lambda: bool(app.analyses))
        values = app.tree.item("ch0", "values")
        assert "둔화" in values[0] and values[2] == "-60.0%"
        assert "상위 성과" in app.summary_box.get("1.0", "end")

        app.check_ollama()
        pump(root, lambda: app.ollama_ok)

        app.generate_now()
        pump(root, lambda: not app.busy and app.generation is not None)
        text = app.result_box.get("1.0", "end")
        assert "## 주제 후보" in text and "## 대본" in text
        assert list(app.topic_combo["values"])[0].startswith("1. ")
        assert app.topic_combo.current() == 1
        first_output = app.generation.output_path
        assert first_output.exists() and first_output.parent.parent == tmp_path / "outputs"

        app.topic_combo.current(0)
        app.rewrite_script()
        pump(root, lambda: not app.busy and app.generation.selected == 0)
        assert app.generation.output_path != first_output

        app._apply_auto_check(True, save=True)
        assert app.scheduler is not None and read_raw(path)["gui"]["auto_check"] is True
        app._apply_auto_check(False, save=True)
        assert app.scheduler is None
        assert not [d for d in dialogs if d[0] == "showerror"], dialogs
        app.close()
    finally:
        server.shutdown()


def test_video_tab_flow(root, dialogs, tmp_path, monkeypatch):
    """주제/대본 생성 → [영상 만들기] 탭으로 가져오기 → 6단계 진행 표시 → 결과 경로/폴더 열기."""
    from fake_video_services import FakeEdgeTTS, make_test_clip, start_fake_pexels
    from yt_monitor.video.ffmpeg import check_ffmpeg
    from yt_monitor.video.pexels import PexelsClient
    from yt_monitor.video.pipeline import VideoPipeline

    ff = check_ffmpeg()
    if not ff.ok:
        pytest.skip("ffmpeg 없음")
    server, url, _ = start_fake_ollama()
    pex, pex_url, _ = start_fake_pexels({"a.mp4": make_test_clip(ff.path, tmp_path / "a.mp4", seconds=1)})
    try:
        path = make_config(tmp_path, url)
        raw = read_raw(path)
        raw["video"].update(width=180, height=320, fps=15, preset="ultrafast", crf=30,
                            subtitle_font_size=20, subtitle_margin_bottom=40)
        raw["pexels"]["api_key"] = "fake"
        save_config(raw, path)
        cfg = load_config(path, load_env=False)
        run_check(cfg, service=FakeYouTube(make_videos(40, 100), T0), notifier=FakeNotifier(),
                  generate=False, now=T0)

        app = gui.App(root, path, check_ollama_on_start=False)
        app.video_pipeline_factory = lambda c: VideoPipeline(
            c, pexels=PexelsClient("fake", api_url=pex_url, target_size=(180, 320)),
            tts=FakeEdgeTTS(retry_delay=0))
        opened = []
        monkeypatch.setattr(gui, "open_path", lambda p, select=False: opened.append((p, select)))
        pump(root, lambda: bool(app.analyses))

        app.make_video()                                   # 대본 없음 → 안내만
        assert dialogs[-1][0] == "showinfo" and app.video_result is None

        app.generate_now()
        pump(root, lambda: not app.busy and app.generation is not None)
        # 생성 결과가 영상 탭에 자동으로 들어온다 (TTS용으로 정리된 대본)
        script = app.video_script.get("1.0", "end")
        assert "편의점" in script and "[효과음]" not in script and "#" not in script
        assert app.video_title_var.get() == app.generation.title

        app.video_script.delete("1.0", "end")
        app.send_to_video()
        assert app.nb.select() == str(app.video_tab) and "편의점" in app.video_script.get("1.0", "end")
        app.video_title_var.set("GUI 테스트 영상")
        app.voice_combo.current(1)

        steps = []
        orig = app._on_video_progress
        app._on_video_progress = lambda n, t, m: (steps.append(n), orig(n, t, m))
        app.make_video()
        assert str(app.btn_generate["state"]) == "disabled"
        pump(root, lambda: not app.busy, timeout=120)
        assert not [d for d in dialogs if d[0] in ("showerror", "showwarning")], dialogs
        assert steps == [1, 2, 3, 4, 5, 6]
        res = app.video_result
        assert res.video_path.exists() and res.video_path.name == "GUI_테스트_영상.mp4"
        assert app.video_path_var.get() == str(res.video_path)
        assert "완료" in app.video_step_var.get()
        assert "6/6" in app.video_log.get("1.0", "end")
        app.btn_open_folder.invoke()
        assert opened == [(res.video_path, True)]
        app.close()
    finally:
        server.shutdown()
        pex.shutdown()


def test_trend_flow_with_auto_video(root, dialogs, tmp_path, monkeypatch):
    """🔥 유행 쇼츠 분석 → 트렌드 탭 표 + 쇼츠 대본 → (자동 체크 시) 영상까지."""
    from fake_video_services import FakeEdgeTTS
    from test_yt_monitor_trends import NOW, FakeTrendYouTube
    from yt_monitor import collector, trends
    from yt_monitor.video.ffmpeg import check_ffmpeg
    from yt_monitor.video.pipeline import VideoPipeline

    if not check_ffmpeg().ok:
        pytest.skip("ffmpeg 없음")
    monkeypatch.setattr(collector, "build_youtube_service", lambda key: FakeTrendYouTube())
    monkeypatch.setattr(trends, "utcnow", lambda: NOW)
    server, url, _ = start_fake_ollama()
    try:
        path = make_config(tmp_path, url)
        raw = read_raw(path)
        raw["video"].update(width=180, height=320, fps=15, preset="ultrafast", crf=30,
                            subtitle_font_size=20, subtitle_margin_bottom=40)
        raw["pexels"]["api_key"] = "fake"
        raw["trends"]["popular_pages"] = 1
        save_config(raw, path)
        app = gui.App(root, path, check_ollama_on_start=False)
        app.video_pipeline_factory = lambda c: VideoPipeline(c, pexels=None, tts=FakeEdgeTTS(retry_delay=0),
                                                             offline=True)
        app.trend_auto_var.set(True)
        app._save_trend_auto()
        assert read_raw(path)["trends"]["auto_video"] is True

        app.trend_now()
        pump(root, lambda: app.video_result is not None and not app.busy, timeout=120)
        assert not [d for d in dialogs if d[0] in ("showerror", "showwarning")], dialogs
        assert "편의점 라면 꿀조합" in app.trend_box.get("1.0", "end")
        assert app.generation.trigger == "trend"
        assert app.video_title_var.get() == app.generation.title
        assert app.video_result.video_path.exists()
        assert app.nb.select() == str(app.video_tab)
        app.close()
    finally:
        server.shutdown()


def test_missing_model_closes_app(root, dialogs, tmp_path):
    server, url, _ = start_fake_ollama()
    try:
        app = gui.App(root, make_config(tmp_path, url, model="없는모델:1b"))
        pump(root, lambda: app.closed)
        assert any("ollama pull 없는모델:1b" in d[1][1] for d in dialogs if d[0] == "showerror")
    finally:
        server.shutdown()


def test_setup_dialog_writes_config(root, dialogs, tmp_path):
    path = tmp_path / "config.yaml"
    dlg = gui.SetupDialog(root, path, first_run=True)
    dlg.vars["api_key"].set("AIza-key")
    dlg.channels_text.insert("1.0", "@첫채널 | 첫 채널\n")
    dlg.vars["interval"].set("4")
    dlg.vars["threshold"].set("25")
    dlg.vars["pexels_key"].set("pexels-key ")
    dlg._save()
    assert dlg.saved
    cfg = load_config(path, load_env=False)
    assert cfg.youtube_api_key == "AIza-key" and cfg.channels[0].label == "첫 채널"
    assert cfg.schedule["interval_hours"] == 4 and cfg.analysis["drop_threshold_pct"] == 25
    assert cfg.pexels["api_key"] == "pexels-key" and cfg.video["width"] == 1080

    bad = gui.SetupDialog(root, tmp_path / "bad.yaml")
    bad._save()                                           # 필수값 없음 → 저장 안 됨
    assert not bad.saved and dialogs[-1][0] == "showerror"
    bad.destroy()


@pytest.mark.skipif(not os.environ.get("YT_SCREENSHOT"), reason="스크린샷 필요할 때만")
def test_screenshot(root, dialogs, tmp_path):
    """YT_SCREENSHOT=경로.png 로 실행하면 메인 창 스크린샷을 저장 (문서용)."""
    server, url, _ = start_fake_ollama()
    try:
        path = make_config(tmp_path, url)
        cfg = load_config(path, load_env=False)
        fake = FakeYouTube(make_videos(40, 100), T0)
        from datetime import timedelta
        for h in range(0, 24 * 15, 6):
            fake.now = T0 + timedelta(hours=h)
            run_check(cfg, service=fake, notifier=FakeNotifier(), generate=False, now=fake.now)
        root.deiconify()
        app = gui.App(root, path)
        pump(root, lambda: app.analyses and app.ollama_ok)
        app.generate_now()
        pump(root, lambda: not app.busy and app.generation is not None)
        root.geometry("1000x700+0+0")
        for _ in range(20):
            root.update()
            time.sleep(0.05)
        os.system(f"import -window root {os.environ['YT_SCREENSHOT']}")
        app.close()
    finally:
        server.shutdown()
