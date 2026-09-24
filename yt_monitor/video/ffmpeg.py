"""ffmpeg 찾기 · 실행 · 로그.

- 찾는 순서: config video.ffmpeg_path → exe(또는 yt_monitor) 폴더의 ffmpeg(.exe) / ffmpeg/bin/ → PATH
- 모든 ffmpeg 실행은 <작업폴더>/logs/NN_단계.log 에 명령줄 + 전체 출력을 남긴다.
  실패하면 FFmpegError 메시지에 마지막 출력 몇 줄과 로그 파일 경로를 넣어서 바로 원인을 볼 수 있게 한다.
- Windows 창 모드 exe에서 ffmpeg를 부를 때 검은 콘솔 창이 뜨지 않도록 CREATE_NO_WINDOW 사용.

단독 확인: python -m yt_monitor.video.ffmpeg
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..paths import app_dir

log = logging.getLogger(__name__)

INSTALL_HELP = """ffmpeg가 설치되어 있지 않습니다. 아래 중 한 가지 방법으로 설치하세요.

[Windows]
  방법 1) 명령 프롬프트(cmd)에서:  winget install Gyan.FFmpeg
          설치 후 프로그램을 다시 실행하세요. (PATH가 새로 잡혀야 합니다)
  방법 2) https://www.gyan.dev/ffmpeg/builds/ 에서 ffmpeg-release-essentials.zip 을 받아
          압축을 풀고, bin 폴더의 ffmpeg.exe 를 YouTubeMonitor.exe 옆에 복사하세요.
  방법 3) 설정(config.yaml)의 video.ffmpeg_path 에 ffmpeg.exe 전체 경로를 적으세요.

[macOS]  brew install ffmpeg
[Ubuntu] sudo apt install ffmpeg

설치 확인: 명령 프롬프트에서  ffmpeg -version"""


class FFmpegError(RuntimeError):
    def __init__(self, message: str, log_path: Path | None = None):
        super().__init__(message)
        self.log_path = log_path


class FFmpegNotFound(FFmpegError):
    pass


class Cancelled(RuntimeError):
    """사용자가 영상 생성을 취소함."""


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def find_ffmpeg(configured: str | None = None) -> str | None:
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    candidates = []
    if configured:
        p = Path(configured).expanduser()
        candidates += [p, p / exe, p / "bin" / exe]
    base = app_dir()
    candidates += [base / exe, base / "ffmpeg" / exe, base / "ffmpeg" / "bin" / exe]
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("ffmpeg")


@dataclass
class FFmpegStatus:
    path: str | None
    version: str = ""
    has_libass: bool = False
    has_libx264: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.path) and self.has_libass and self.has_libx264 and not self.error

    @property
    def message(self) -> str:
        if not self.path:
            return INSTALL_HELP
        if self.error:
            return f"ffmpeg 실행 실패 ({self.path}): {self.error}"
        missing = [n for n, ok in (("libass(자막 번인)", self.has_libass),
                                   ("libx264(H.264 인코더)", self.has_libx264)) if not ok]
        if missing:
            return (f"설치된 ffmpeg({self.path})에 {', '.join(missing)} 기능이 없습니다.\n"
                    "gyan.dev의 essentials/full 빌드처럼 기능이 포함된 ffmpeg를 설치하세요.\n\n"
                    + INSTALL_HELP)
        return f"ffmpeg 준비 완료 ({self.version})"


def check_ffmpeg(configured: str | None = None) -> FFmpegStatus:
    path = find_ffmpeg(configured)
    if not path:
        return FFmpegStatus(None)
    try:
        out = subprocess.run([path, "-hide_banner", "-version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20,
                             creationflags=_no_window())
        filters = subprocess.run([path, "-hide_banner", "-filters"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20,
                                 creationflags=_no_window())
        encoders = subprocess.run([path, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=20,
                                  creationflags=_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        return FFmpegStatus(path, error=str(exc))
    first = (out.stdout or "").splitlines()[:1]
    version = first[0].replace("ffmpeg version ", "").split(" Copyright")[0] if first else "?"
    return FFmpegStatus(path, version,
                        has_libass=bool(re.search(r"^\s*\S+\s+ass\s", filters.stdout, re.M)),
                        has_libx264=" libx264 " in encoders.stdout)


class FFmpegRunner:
    """ffmpeg 호출을 단계별 로그 파일로 남기는 실행기."""

    def __init__(self, ffmpeg: str, log_dir: Path, cancel: threading.Event | None = None):
        self.ffmpeg = ffmpeg
        self.log_dir = Path(log_dir)
        self.cancel = cancel
        self._n = 0

    @classmethod
    def from_config(cls, video_cfg: dict, log_dir: Path, cancel: threading.Event | None = None):
        path = find_ffmpeg(video_cfg.get("ffmpeg_path"))
        if not path:
            raise FFmpegNotFound(INSTALL_HELP)
        return cls(path, log_dir, cancel)

    def run(self, args: list[str], step: str, cwd: Path | None = None, timeout: float = 1800) -> str:
        """ffmpeg <args> 실행. 실패하면 FFmpegError. 반환값은 ffmpeg 출력(stderr)."""
        self._n += 1
        self.log_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w-]+", "_", step)
        log_path = self.log_dir / f"{self._n:02d}_{slug}.log"
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", *[str(a) for a in args]]
        cmdline = subprocess.list2cmdline(cmd)
        log.info("ffmpeg [%s]: %s", step, cmdline)
        started = time.monotonic()
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, creationflags=_no_window())
        except OSError as exc:
            raise FFmpegError(f"ffmpeg 실행 실패 ({step}): {exc}\n명령: {cmdline}") from exc

        out = err = b""
        while True:
            try:
                out, err = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if (self.cancel is not None and self.cancel.is_set()) or \
                        time.monotonic() - started > timeout:
                    proc.kill()
                    out, err = proc.communicate()
                    if self.cancel is not None and self.cancel.is_set():
                        raise Cancelled("사용자가 영상 생성을 취소했습니다.") from None
                    raise FFmpegError(f"ffmpeg 시간 초과 ({step}, {timeout:.0f}초)\n로그: {log_path}",
                                      log_path) from None
        text = err.decode("utf-8", errors="replace")
        elapsed = time.monotonic() - started
        log_path.write_text(
            f"# 단계: {step}\n# 작업 폴더: {cwd or Path.cwd()}\n# 종료 코드: {proc.returncode}"
            f" ({elapsed:.1f}초)\n# 명령:\n{cmdline}\n\n{text}", encoding="utf-8")
        if proc.returncode != 0:
            tail = "\n".join(line for line in text.strip().splitlines()[-15:])
            log.error("ffmpeg 실패 [%s] (코드 %s) — 로그: %s\n%s", step, proc.returncode, log_path, tail)
            raise FFmpegError(f"ffmpeg 오류: {step} (종료 코드 {proc.returncode})\n\n{tail}\n\n"
                              f"전체 로그: {log_path}", log_path)
        return text


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: (Video|Audio): ([^\n]+)")


@dataclass
class MediaInfo:
    duration: float | None
    width: int | None = None
    height: int | None = None
    has_audio: bool = False
    has_video: bool = False


def probe(ffmpeg: str, path: Path) -> MediaInfo:
    """ffprobe 없이 `ffmpeg -i` 출력으로 길이/해상도/스트림을 읽는다."""
    res = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True,
                         creationflags=_no_window())
    text = res.stderr.decode("utf-8", errors="replace")
    m = _DURATION_RE.search(text)
    duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    info = MediaInfo(duration)
    for kind, rest in _STREAM_RE.findall(text):
        if kind == "Audio":
            info.has_audio = True
        elif not info.has_video:
            info.has_video = True
            if wh := re.search(r"\b(\d{2,5})x(\d{2,5})\b", rest):
                info.width, info.height = int(wh.group(1)), int(wh.group(2))
    return info


if __name__ == "__main__":
    st = check_ffmpeg(sys.argv[1] if len(sys.argv) > 1 else None)
    print(st.message)
    if st.path:
        print(f"경로: {st.path}\nlibass: {st.has_libass}, libx264: {st.has_libx264}")
    raise SystemExit(0 if st.ok else 1)
