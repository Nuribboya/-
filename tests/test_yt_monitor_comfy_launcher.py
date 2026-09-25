"""ComfyUI 자동 실행/종료 · 폴더 찾기 · 모델별 자동 설정 테스트."""

import socket
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_ollama import start_fake_ollama  # noqa: E402
from fake_video_services import FakeEdgeTTS, start_fake_comfy  # noqa: E402
from yt_monitor.config import Config, read_raw  # noqa: E402
from yt_monitor.ollama_client import OllamaClient  # noqa: E402
from yt_monitor.video.comfy_launcher import (ComfyLauncher, diagnose, find_comfy_dir,  # noqa: E402
                                             launch_command, list_checkpoint_files)
from yt_monitor.video.comfyui import ComfyClient, tuned_params  # noqa: E402
from yt_monitor.video.ffmpeg import check_ffmpeg  # noqa: E402
from yt_monitor.video.pipeline import VideoPipeline  # noqa: E402

TESTS = Path(__file__).resolve().parent
FF = check_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(not FF.ok, reason="ffmpeg 없음")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_portable(root: Path, png_path: Path | None = None, models=("sdxl_lightning_4step.safetensors",)) -> Path:
    """ComfyUI portable 폴더 흉내: python_embeded/python(=현재 파이썬) + ComfyUI/main.py(가짜 서버)."""
    d = root / "ComfyUI_windows_portable"
    (d / "python_embeded").mkdir(parents=True)
    py = d / "python_embeded" / ("python.exe" if sys.platform == "win32" else "python")
    try:
        py.symlink_to(sys.executable)
    except OSError:                       # Windows는 심볼릭 링크에 관리자 권한이 필요 → 실행 테스트만 건너뜀
        py.write_bytes(b"")
    (d / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True)
    for m in models:
        (d / "ComfyUI" / "models" / "checkpoints" / m).write_bytes(b"x")
    (d / "ComfyUI" / "main.py").write_text(f"""
import sys, time
sys.path[:0] = [{str(TESTS)!r}, {str(TESTS.parent)!r}]
from fake_video_services import start_fake_comfy
port = int(sys.argv[sys.argv.index("--port") + 1])
png = open({str(png_path or '')!r}, "rb").read() if {bool(png_path)!r} else b"PNG"
print("Starting server", flush=True)
start_fake_comfy(png, checkpoints={tuple(models)!r}, port=port)
while True:
    time.sleep(1)
""", encoding="utf-8")
    return d


def test_find_comfy_dir(tmp_path):
    root = tmp_path / "Downloads" / "ComfyUI_windows_portable_nvidia"
    root.mkdir(parents=True)
    d = make_portable(root)
    # 다운로드\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable (두 단계) 자동 탐색
    assert find_comfy_dir(None, extra=[tmp_path / "Downloads"]) == d
    # 설정에 상위 폴더나 ComfyUI\ 를 넣어도 찾는다
    assert find_comfy_dir(str(d)) == d and find_comfy_dir(d / "ComfyUI") == d and find_comfy_dir(root) == d
    assert find_comfy_dir(tmp_path / "nothing") is None
    # 압축 풀기로 같은 이름 폴더가 한 단계 더 생긴 경우 (3단계)
    deep = tmp_path / "Deep" / "ComfyUI_windows_portable_nvidia" / "ComfyUI_windows_portable_nvidia"
    deep.mkdir(parents=True)
    d3 = make_portable(deep)
    assert find_comfy_dir(None, extra=[tmp_path / "Deep"]) == d3
    assert list_checkpoint_files(d) == ["sdxl_lightning_4step.safetensors"]
    cmd = launch_command(d, "http://127.0.0.1:8190")
    assert cmd[1:4] == ["-s", str(d / "ComfyUI" / "main.py"), "--windows-standalone-build"]
    assert cmd[-2:] == ["--port", "8190"] and "--port" not in launch_command(d, "http://127.0.0.1:8188")


def test_tuned_params_for_lightning():
    assert tuned_params("sdxl_lightning_4step.safetensors", 6, 2.0, "dpmpp_sde", "karras") == (4, 1.0, "euler",
                                                                                              "sgm_uniform")
    assert tuned_params("sdxl_lightning_8step.safetensors", 6, 2.0, "a", "b")[0] == 8
    assert tuned_params("juggernautXL.safetensors", 30, 6.0, "dpmpp_2m", "karras") == (30, 6.0, "dpmpp_2m", "karras")
    assert tuned_params("juggernautXL_lightning.safetensors", 6, 2.0, "a", "b") == (5, 1.5, "dpmpp_sde", "karras")
    assert tuned_params("RealVisXL_V5.0_Lightning_fp16.safetensors", 6, 2.0, "a", "b") == (5, 1.5, "dpmpp_sde",
                                                                                          "karras")


def test_realistic_model_preferred():
    from yt_monitor.video.comfyui import pick_checkpoint

    assert pick_checkpoint(["sdxl_lightning_4step.safetensors", "RealVisXL_V5.0_Lightning_fp16.safetensors"]) \
        == "RealVisXL_V5.0_Lightning_fp16.safetensors"
    assert pick_checkpoint(["a.safetensors", "b.safetensors"]) == "a.safetensors" and pick_checkpoint([]) == ""


def test_old_ai_style_upgraded_to_realistic(tmp_path):
    from yt_monitor.config import (OLD_AI_STYLE, REALISTIC_NEGATIVE, REALISTIC_STYLE, read_raw,
                                   save_config)

    raw = read_raw(tmp_path / "x.yaml")
    raw["config_version"] = 2
    raw["ai_images"]["style"] = OLD_AI_STYLE
    raw["ai_images"]["negative"] = "my own negative"
    raw["channels"] = [{"handle": "@x"}]
    path = save_config(raw, tmp_path / "config.yaml")
    up = read_raw(path)
    assert up["ai_images"]["style"] == REALISTIC_STYLE and up["ai_images"]["negative"] == "my own negative"
    assert REALISTIC_NEGATIVE.startswith("cgi, 3d render") and up["config_version"] == 3


def test_generate_uses_tuned_params(tmp_path):
    server, url, handler = start_fake_comfy(b"PNG", checkpoints=("sdxl_lightning_4step.safetensors",))
    try:
        ComfyClient(url, poll_interval=0.01).generate("a shark", tmp_path / "a.png")
        k = handler.prompts[-1]["3"]["inputs"]
        assert (k["steps"], k["cfg"], k["sampler_name"], k["scheduler"]) == (4, 1.0, "euler", "sgm_uniform")
        ComfyClient(url, poll_interval=0.01, auto_tune=False, steps=6).generate("a shark", tmp_path / "b.png")
        assert handler.prompts[-1]["3"]["inputs"]["steps"] == 6
    finally:
        server.shutdown()


def test_diagnose_messages(tmp_path):
    down = {"host": "http://127.0.0.1:9", "comfy_dir": str(tmp_path / "none")}
    ok, text = diagnose(down)
    assert not ok and "폴더도 찾지 못했습니다" in text
    empty = make_portable(tmp_path / "a", models=())
    ok, text = diagnose({**down, "comfy_dir": str(empty)})
    assert not ok and str(empty / "ComfyUI" / "models" / "checkpoints") in text
    ready = make_portable(tmp_path / "b")
    ok, text = diagnose({**down, "comfy_dir": str(ready)})
    assert ok and "자동으로 켰다가" in text and "sdxl_lightning_4step" in text
    server, url, _ = start_fake_comfy(b"PNG", checkpoints=("sdxl_lightning_4step.safetensors",))
    try:
        ok, text = diagnose({"host": url})
        assert ok and "준비 완료" in text and "4스텝 · cfg 1 · euler" in text
    finally:
        server.shutdown()


def can_launch(d: Path) -> bool:
    return any(p.is_symlink() for p in (d / "python_embeded").iterdir())


def test_launcher_starts_and_stops(tmp_path):
    d = make_portable(tmp_path)
    if not can_launch(d):
        pytest.skip("심볼릭 링크를 만들 수 없음")
    port = free_port()
    host = f"http://127.0.0.1:{port}"
    launcher = ComfyLauncher(d, host, log_path=tmp_path / "logs" / "comfyui.log", timeout=30, poll_interval=0.2)
    assert launcher.start()
    assert requests.get(f"{host}/system_stats", timeout=3).ok
    launcher.stop()
    with pytest.raises(requests.RequestException):
        requests.get(f"{host}/system_stats", timeout=2)
    assert "Starting server" in (tmp_path / "logs" / "comfyui.log").read_text(encoding="utf-8")


@needs_ffmpeg
def test_pipeline_auto_starts_and_stops_comfy(tmp_path):
    png_path = tmp_path / "ai.png"
    import subprocess
    subprocess.run([FF.path, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=96x160",
                    "-frames:v", "1", str(png_path)], check=True)
    d = make_portable(tmp_path / "portable", png_path)
    if not can_launch(d):
        pytest.skip("심볼릭 링크를 만들 수 없음")
    port = free_port()
    raw = read_raw(tmp_path / "config.yaml")
    raw["language"] = "ko"
    raw["video"].update(width=180, height=320, fps=15, crf=30, preset="ultrafast", clip_max_seconds=1.5,
                        subtitle_font_size=20, subtitle_margin_bottom=40, hook_font_size=24)
    raw["ai_images"].update(enabled=True, host=f"http://127.0.0.1:{port}", comfy_dir=str(d), mode="all")
    cfg = Config(raw=raw, base_dir=tmp_path, path=tmp_path / "config.yaml")
    ol, ol_url, _ = start_fake_ollama()
    try:
        statuses = []
        pipe = VideoPipeline(cfg, tts=FakeEdgeTTS(retry_delay=0), ollama=OllamaClient(ol_url, "qwen2.5:7b"))
        res = pipe.run("편의점에서 이 조합 먹어보셨나요? 진짜 맛있어요. 마지막이 반전입니다.", "자동 실행",
                       on_status=statuses.append)
        assert any("ComfyUI 자동 실행 중" in m for m in statuses) and any("ComfyUI 종료" in m for m in statuses)
        assert not any("AI 이미지" in w for w in res.warnings), res.warnings
        assert any("AI 이미지" in m and "생성" in m for m in statuses)
        with pytest.raises(requests.RequestException):          # 자동으로 켠 것은 자동으로 꺼진다
            requests.get(f"http://127.0.0.1:{port}/system_stats", timeout=2)
        assert (res.work_dir / "logs" / "comfyui.log").exists()
    finally:
        ol.shutdown()
