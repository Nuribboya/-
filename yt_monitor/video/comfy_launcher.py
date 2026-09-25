"""ComfyUI 자동 실행/종료: 영상 만들 때만 창 없이 켰다가, AI 이미지를 다 만들면 끈다.

- ComfyUI 폴더(portable)는 설정 ai_images.comfy_dir 에 넣거나, 비워 두면 흔한 위치에서 자동으로 찾는다:
  exe 옆 · C:\\ · D:\\ · 다운로드 · 바탕화면 · 문서 (ComfyUI* 폴더를 두 단계까지)
- 폴더 조건: python_embeded\\python.exe 와 ComfyUI\\main.py 가 있는 곳
- 사용자가 직접 켜 둔 ComfyUI는 건드리지 않는다 (프로그램이 켠 것만 끈다)
- ComfyUI 출력은 logs/comfyui.log 에 남긴다 (문제 생기면 이 파일 확인)
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def is_comfy_dir(path: Path) -> bool:
    p = Path(path)
    return (p / "ComfyUI" / "main.py").is_file() and (p / "python_embeded").is_dir()


def _candidates(extra: list[Path] | None = None) -> list[Path]:
    home = Path.home()
    bases = [*(extra or []), Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd(),
             Path("C:/"), Path("D:/"), home / "Downloads", home / "Desktop", home / "Documents",
             home / "OneDrive" / "Desktop", home / "OneDrive" / "문서", home / "바탕 화면", home]
    out: list[Path] = []
    for base in bases:
        try:
            if not base.is_dir():
                continue
            out.append(base)
            # 압축을 풀면 ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable
            # 처럼 한 단계 더 생기는 경우가 있어서 3단계까지 본다
            level = [p for p in sorted(base.glob("ComfyUI*")) if p.is_dir()]
            for _ in range(3):
                out += level
                level = [q for p in level for q in sorted(p.glob("ComfyUI*")) if q.is_dir()]
        except OSError:
            continue
    return out


def find_comfy_dir(configured: str | Path | None = None, extra: list[Path] | None = None) -> Path | None:
    """설정한 폴더 → 흔한 위치 순으로 ComfyUI portable 폴더를 찾는다."""
    if configured:
        p = Path(str(configured).strip().strip('"'))
        for cand in (p, p.parent, p / "ComfyUI_windows_portable"):   # ComfyUI\ 나 상위 폴더를 넣어도 되게
            if is_comfy_dir(cand):
                return cand
        log.warning("설정한 ComfyUI 폴더가 올바르지 않음: %s → 자동으로 찾아봅니다", configured)
    for cand in _candidates(extra):
        if is_comfy_dir(cand):
            return cand
    return None


def list_checkpoint_files(comfy_dir: Path) -> list[str]:
    """ComfyUI를 켜지 않고 models/checkpoints 폴더의 모델 파일 이름을 본다."""
    folder = Path(comfy_dir) / "ComfyUI" / "models" / "checkpoints"
    try:
        return sorted(p.name for p in folder.rglob("*") if p.suffix.lower() in (".safetensors", ".ckpt"))
    except OSError:
        return []


def _port(host: str) -> int:
    return urlparse(host if "://" in host else f"http://{host}").port or 8188


def launch_command(comfy_dir: Path, host: str) -> list[str]:
    """run_nvidia_gpu.bat 과 같은 방식 (포트가 기본값이 아니면 --port 추가)."""
    d = Path(comfy_dir)
    python = d / "python_embeded" / ("python.exe" if sys.platform == "win32" else "python")
    cmd = [str(python), "-s", str(d / "ComfyUI" / "main.py"), "--windows-standalone-build"]
    port = _port(host)
    if port != 8188:
        cmd += ["--port", str(port)]
    return cmd


class ComfyLauncher:
    def __init__(self, comfy_dir: Path, host: str, *, log_path: Path | None = None, timeout: float = 240,
                 command: list[str] | None = None, poll_interval: float = 1.0):
        self.comfy_dir = Path(comfy_dir)
        self.host = host.rstrip("/")
        self.log_path = log_path
        self.timeout = timeout
        self.command = command or launch_command(self.comfy_dir, host)
        self.poll_interval = poll_interval
        self.proc: subprocess.Popen | None = None
        self._log_file = None

    @classmethod
    def from_config(cls, ai_cfg: dict, log_dir: Path | None = None) -> "ComfyLauncher | None":
        d = find_comfy_dir(ai_cfg.get("comfy_dir"))
        if d is None:
            return None
        return cls(d, ai_cfg.get("host") or "http://127.0.0.1:8188",
                   log_path=(Path(log_dir) / "comfyui.log") if log_dir else None,
                   timeout=float(ai_cfg.get("start_timeout_sec", 240)))

    def _up(self) -> bool:
        try:
            s = requests.Session()
            s.trust_env = False
            return s.get(f"{self.host}/system_stats", timeout=3).ok
        except requests.RequestException:
            return False

    def start(self, cancel: threading.Event | None = None,
              on_status: Callable[[str], None] | None = None) -> bool:
        """창 없이 실행하고 서버가 뜰 때까지 기다린다. 실패하면 False (프로세스는 정리)."""
        if self._up():
            return True
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.log_path, "w", encoding="utf-8", errors="replace")
        kw: dict = {"cwd": str(self.comfy_dir), "stdin": subprocess.DEVNULL,
                    "stdout": self._log_file or subprocess.DEVNULL, "stderr": subprocess.STDOUT}
        if sys.platform == "win32":
            kw["creationflags"] = CREATE_NO_WINDOW
        else:
            kw["start_new_session"] = True
        log.info("ComfyUI 실행: %s", self.command)
        try:
            self.proc = subprocess.Popen(self.command, **kw)
        except OSError as exc:
            log.warning("ComfyUI 실행 실패: %s", exc)
            self._close_log()
            return False
        started = time.monotonic()
        last_note = started
        while time.monotonic() - started < self.timeout:
            if cancel is not None and cancel.is_set():
                self.stop()
                raise InterruptedError
            if self.proc.poll() is not None:
                log.warning("ComfyUI가 바로 종료됨 (코드 %s) — %s 확인", self.proc.returncode, self.log_path)
                self._close_log()
                return False
            if self._up():
                return True
            if on_status and time.monotonic() - last_note >= 15:
                last_note = time.monotonic()
                on_status(f"  ComfyUI 준비 중… {time.monotonic() - started:.0f}초")
            time.sleep(self.poll_interval)
        self.stop()
        return False

    def stop(self):
        """이 객체가 켠 ComfyUI만 끈다 (하위 프로세스까지)."""
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                                   creationflags=CREATE_NO_WINDOW, timeout=20)
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=20)
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                except OSError:
                    pass
        self._close_log()

    def _close_log(self):
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


def diagnose(ai_cfg: dict) -> tuple[bool, str]:
    """설정 창 [연결 확인]용: 켜져 있는지 · 폴더 · 모델 파일까지 한 번에 알려준다."""
    from .comfyui import ComfyClient, tuned_params

    st = ComfyClient.from_config(ai_cfg).status()
    d = find_comfy_dir(ai_cfg.get("comfy_dir"))
    ckpt_dir = (d / "ComfyUI" / "models" / "checkpoints") if d else None
    files = list_checkpoint_files(d) if d else []
    lines: list[str] = []
    if st.ok:
        lines.append(st.message)
        if ai_cfg.get("auto_tune", True):
            steps, cfg, sampler, sched = tuned_params(st.checkpoint, int(ai_cfg.get("steps", 6)),
                                                      float(ai_cfg.get("cfg", 2.0)), ai_cfg.get("sampler", ""),
                                                      ai_cfg.get("scheduler", ""))
            lines.append(f"자동 설정: {steps}스텝 · cfg {cfg:g} · {sampler} · {sched}")
        ok = True
    elif st.server_up:
        lines.append(st.message)
        if ckpt_dir:
            lines.append(f"\n모델 파일(.safetensors)을 이 폴더에 넣고 ComfyUI를 다시 켜세요:\n{ckpt_dir}")
        ok = False
    elif d is None:
        lines.append("ComfyUI가 꺼져 있고, ComfyUI 폴더도 찾지 못했습니다.\n"
                     "설정의 'ComfyUI 폴더'에 run_nvidia_gpu.bat 이 있는 폴더를 지정하세요.")
        ok = False
    elif not files:
        lines.append(f"ComfyUI 폴더는 찾았지만 모델 파일이 없습니다.\n\n"
                     f"sdxl_lightning_4step.safetensors 를 이 폴더에 넣으세요:\n{ckpt_dir}")
        ok = False
    else:
        auto = ai_cfg.get("auto_start", True)
        lines.append(f"ComfyUI는 꺼져 있지만 준비됐습니다. ✅\n폴더: {d}\n모델: {', '.join(files)}\n\n" +
                     ("영상 만들 때 창 없이 자동으로 켰다가, 다 만들면 자동으로 끕니다."
                      if auto else "auto_start가 꺼져 있어 영상 만들기 전에 직접 켜야 합니다."))
        ok = bool(auto)
    if st.server_up and st.checkpoints:
        lines.append(f"\n설치된 모델: {', '.join(st.checkpoints)}")
    return ok, "\n".join(lines)
