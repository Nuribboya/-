"""ffmpeg 합성: 씬별 클립 → 이어붙이기 → 음성 삽입 → 자막 번인 → 9:16 mp4.

단계별 결과 파일 (작업 폴더):
    shots/shot_001.mp4 ...   각 클립을 1080x1920 · 30fps · 정해진 프레임 수로 맞춘 것
                             (짧으면 -stream_loop 로 반복, 길면 잘라냄, 비율이 다르면 가운데를 채워 자름)
    01_clips.mp4             클립 이어붙이기 (소리 없음)
    02_with_audio.mp4        + TTS 음성 (배경음악 없음)
    최종 mp4                  + 자막 번인 (ASS, 하단 중앙 · 굵은 글씨 · 검은 박스)
    logs/NN_*.log            ffmpeg 명령줄과 전체 출력 (에러 디버깅용)

프레임 경계는 씬 시작/끝 시각을 fps 격자에 반올림해서 정하므로 클립이 많아도 음성과 싱크가 밀리지 않는다.

단독 테스트 (인터넷/API 키 없이 테스트 패턴 영상 + 테스트 음으로 단계별 결과물 생성):
    python -m yt_monitor.video.compose --out samples/compose
    python -m yt_monitor.video.compose --out samples/compose --step concat    # 1단계만
    python -m yt_monitor.video.compose --clips a.mp4 b.mp4 --audio tts.mp3 --out samples/compose
"""

from __future__ import annotations

import logging
import math
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import FFmpegError, FFmpegRunner

log = logging.getLogger(__name__)

BACKGROUND_COLORS = ["0x1f2a44", "0x2d1f44", "0x1f4435", "0x44301f", "0x3a3a3a"]


@dataclass
class Shot:
    frames: int
    source: Path | None = None     # None → 단색 배경 (스톡 영상을 못 찾았을 때)
    seek: float = 0.0              # 원본에서 시작할 위치(초). 같은 영상을 두 번 쓸 때 다른 장면이 나오게
    color: str = BACKGROUND_COLORS[0]
    zoom_out: bool = False         # True면 확대된 상태에서 천천히 빠진다 (컷마다 번갈아 → 단조롭지 않게)

    @property
    def is_image(self) -> bool:
        return self.source is not None and Path(self.source).suffix.lower() in IMAGE_EXTS


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def split_frames(boundaries: list[float], fps: int, clip_max_seconds: float) -> list[list[int]]:
    """씬 경계 시각 [0, t1, t2, ..., T] → 씬마다 클립별 프레임 수 목록.

    씬이 clip_max_seconds 보다 길면 똑같은 길이의 클립 여러 개로 나눈다 ("씬당 클립 길이").
    모든 경계를 fps 격자에 반올림하므로 프레임 합계 = round(T * fps).
    """
    out = []
    for a, b in zip(boundaries, boundaries[1:]):
        n = max(1, math.ceil((b - a) / clip_max_seconds - 1e-6)) if clip_max_seconds > 0 else 1
        cuts = [round((a + (b - a) * k / n) * fps) for k in range(n + 1)]
        frames = [y - x for x, y in zip(cuts, cuts[1:]) if y > x]
        out.append(frames or [1])
    return out


# ---- 오디오 (wave 모듈로 정확하게) -------------------------------------------------------

def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def concat_wavs(paths: list[Path], out: Path) -> float:
    """같은 형식의 wav들을 이어 붙인다 → 전체 길이(초)."""
    params = None
    with wave.open(str(out), "wb") as dst:
        for p in paths:
            with wave.open(str(p), "rb") as src:
                if params is None:
                    params = src.getparams()
                    dst.setparams(params)
                elif src.getparams()[:3] != params[:3]:
                    raise FFmpegError(f"wav 형식이 다릅니다: {p}")
                dst.writeframes(src.readframes(src.getnframes()))
    return wav_duration(out)


class Composer:
    def __init__(self, runner: FFmpegRunner, work_dir: Path, video_cfg: dict):
        self.runner = runner
        self.work = Path(work_dir)
        self.work.mkdir(parents=True, exist_ok=True)
        self.w = int(video_cfg["width"])
        self.h = int(video_cfg["height"])
        self.fps = int(video_cfg.get("fps") or 30)
        self.crf = str(video_cfg.get("crf") or 21)
        self.preset = str(video_cfg.get("preset") or "veryfast")
        # 스톡 영상이 밋밋해 보이지 않게: 천천히 줌 + 대비/채도 강화
        self.zoom = float(video_cfg.get("zoom", 0.08) or 0)
        self.contrast = float(video_cfg.get("contrast", 1.08) or 1)
        self.saturation = float(video_cfg.get("saturation", 1.2) or 1)

    def _encode_args(self) -> list[str]:
        return ["-c:v", "libx264", "-preset", self.preset, "-crf", self.crf, "-pix_fmt", "yuv420p",
                "-r", str(self.fps)]

    # 오디오 -----------------------------------------------------------------------
    def decode_audio(self, src: Path, out: Path, sample_rate: int = 24000) -> float:
        """TTS mp3 → mono 16bit wav (길이를 샘플 단위로 정확히 알기 위해)."""
        self.runner.run(["-i", src, "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", out],
                        f"음성 변환 {Path(src).name}")
        return wav_duration(out)

    # 1) 클립 맞추기 ------------------------------------------------------------------
    def shot_filter(self, shot: Shot) -> str:
        """크기 맞추기 → (줌) → 색감. 줌은 매 프레임 크기를 키운 뒤 가운데를 잘라내는 방식 (zoompan보다 2배 빠름)."""
        w, h = self.w, self.h
        parts = [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}", f"fps={self.fps}", "setpts=PTS-STARTPTS"]
        if self.zoom > 0 and shot.source is not None:
            dur = max(shot.frames / self.fps, 0.1)
            z = f"(1+{self.zoom:g}*(1-t/{dur:.3f}))" if shot.zoom_out else f"(1+{self.zoom:g}*t/{dur:.3f})"
            parts += [f"scale=w='trunc({w}*{z}/2)*2':h='trunc({h}*{z}/2)*2':eval=frame", f"crop={w}:{h}"]
        if shot.source is not None and (self.contrast != 1 or self.saturation != 1):
            parts.append(f"eq=contrast={self.contrast:g}:saturation={self.saturation:g}")
        return ",".join(parts + ["setsar=1", "format=yuv420p"])

    def prepare_shot(self, shot: Shot, out: Path) -> Path:
        vf = self.shot_filter(shot)
        if shot.source is None:
            inputs = ["-f", "lavfi", "-i", f"color=c={shot.color}:s={self.w}x{self.h}:r={self.fps}"]
        elif shot.is_image:                                     # AI 이미지 등 정지 사진 → 줌으로 움직임
            inputs = ["-loop", "1", "-framerate", str(self.fps), "-i", shot.source]
        else:
            inputs = ["-stream_loop", "-1"]                    # 클립이 짧으면 반복
            if shot.seek > 0:
                inputs += ["-ss", f"{shot.seek:.3f}"]
            inputs += ["-i", shot.source]
        self.runner.run([*inputs, "-frames:v", str(shot.frames), "-vf", vf, "-an",
                         *self._encode_args(), out], f"클립 맞추기 {Path(out).stem}")
        return out

    def prepare_shots(self, shots: list[Shot], on_progress=None) -> list[Path]:
        shot_dir = self.work / "shots"
        shot_dir.mkdir(exist_ok=True)
        paths = []
        for i, shot in enumerate(shots, 1):
            if on_progress:
                on_progress(i, len(shots))
            try:
                paths.append(self.prepare_shot(shot, shot_dir / f"shot_{i:03d}.mp4"))
            except FFmpegError as exc:
                if shot.source is None:
                    raise
                # 손상된 다운로드 등 → 그 클립만 단색 배경으로 대체하고 계속
                log.warning("클립 %s 처리 실패 → 단색 배경으로 대체: %s", shot.source, exc)
                paths.append(self.prepare_shot(Shot(shot.frames, None, color=shot.color),
                                               shot_dir / f"shot_{i:03d}.mp4"))
        return paths

    # 2) 이어붙이기 -------------------------------------------------------------------
    def concat(self, shot_paths: list[Path], out: Path | None = None) -> Path:
        out = out or self.work / "01_clips.mp4"
        lst = self.work / "concat.txt"
        lst.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in shot_paths),
                       encoding="utf-8")
        self.runner.run(["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", out],
                        "클립 이어붙이기", cwd=self.work)
        return out

    # 3) 음성 삽입 --------------------------------------------------------------------
    def add_audio(self, video: Path, audio: Path, duration: float, out: Path | None = None) -> Path:
        out = out or self.work / "02_with_audio.mp4"
        self.runner.run(["-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.3f}",
                         "-movflags", "+faststart", out], "음성 삽입")
        return out

    # 4) 자막 번인 --------------------------------------------------------------------
    def burn_subtitles(self, video: Path, ass_path: Path, out: Path) -> Path:
        """ass 필터 경로는 Windows 드라이브 문자(C:) 이스케이프 문제가 있어서,
        작업 폴더를 cwd로 두고 파일 이름만 넘긴다."""
        ass_path = Path(ass_path)
        local = self.work / "subtitles.ass"
        if ass_path.resolve() != local.resolve():
            shutil.copyfile(ass_path, local)
        self.runner.run(["-i", Path(video).resolve(), "-vf", "ass=subtitles.ass",
                         *self._encode_args(), "-c:a", "copy", "-movflags", "+faststart",
                         Path(out).resolve()], "자막 번인", cwd=self.work)
        return out


# ---- 단독 테스트 -----------------------------------------------------------------------

def _make_test_sources(runner: FFmpegRunner, out: Path) -> tuple[list[Path], Path]:
    """테스트 패턴 영상 3개(가로·세로·아주 짧은 것) + 3구간 테스트 음(총 9초)."""
    src = out / "test_sources"
    src.mkdir(parents=True, exist_ok=True)
    specs = [("testsrc2=s=1280x720:r=25", 6, "a_landscape.mp4"),     # 가로 → 가운데 잘라 세로로
             ("smptebars=s=720x1280:r=30", 2, "b_portrait_short.mp4"),  # 짧음 → 반복
             ("mandelbrot=s=640x640:r=30", 3, "c_square.mp4")]
    clips = []
    for spec, dur, name in specs:
        p = src / name
        if not p.exists():
            runner.run(["-f", "lavfi", "-i", spec, "-t", str(dur), "-c:v", "libx264", "-preset",
                        "ultrafast", "-pix_fmt", "yuv420p", p], f"테스트 영상 {name}")
        clips.append(p)
    audio = src / "tone.wav"
    runner.run(["-f", "lavfi", "-i", "sine=f=440:d=3", "-f", "lavfi", "-i", "sine=f=660:d=2.5",
                "-f", "lavfi", "-i", "sine=f=550:d=3.5", "-filter_complex",
                "[0][1][2]concat=n=3:v=0:a=1,volume=0.2", "-ac", "1", "-ar", "24000", audio], "테스트 음")
    return clips, audio


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .ffmpeg import check_ffmpeg, probe
    from .subtitles import Cue, to_ass, to_srt

    parser = argparse.ArgumentParser(description="ffmpeg 합성 단계별 단독 테스트")
    parser.add_argument("--out", default="samples/compose")
    parser.add_argument("--step", choices=["concat", "audio", "subs", "all"], default="all",
                        help="concat=클립 이어붙이기까지, audio=음성 삽입까지, subs/all=자막까지")
    parser.add_argument("--clips", nargs="*", help="사용할 영상 파일들 (생략하면 테스트 패턴)")
    parser.add_argument("--audio", help="사용할 음성 파일 (생략하면 테스트 음)")
    parser.add_argument("--font", default="Malgun Gothic")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    st = check_ffmpeg()
    if not st.ok:
        print(st.message)
        return 1
    out = Path(args.out).resolve()
    runner = FFmpegRunner(st.path, out / "logs")
    cfg = {"width": 1080, "height": 1920, "fps": 30, "crf": 23, "preset": "veryfast",
           "subtitle_font": args.font}
    comp = Composer(runner, out, cfg)

    clips, audio = _make_test_sources(runner, out)
    if args.clips:
        clips = [Path(c) for c in args.clips]
    if args.audio:
        audio = Path(args.audio)
    wav = out / "narration.wav"
    total = comp.decode_audio(audio, wav)
    # 음성을 클립 수만큼 균등하게 나눠 씬으로 사용 (실제 파이프라인은 TTS 문장 길이로 나눔)
    bounds = [total * i / len(clips) for i in range(len(clips) + 1)]
    shots = []
    for i, frames in enumerate(split_frames(bounds, comp.fps, 4.0)):
        shots += [Shot(f, clips[i], color=BACKGROUND_COLORS[i % 5]) for f in frames]
    print(f"음성 {total:.2f}초 → 클립 {len(shots)}개 (프레임 {[s.frames for s in shots]})")

    v1 = comp.concat(comp.prepare_shots(shots))
    print(f"[1] 클립 이어붙이기: {v1}  {probe(st.path, v1)}")
    if args.step == "concat":
        return 0
    v2 = comp.add_audio(v1, wav, total)
    print(f"[2] 음성 삽입: {v2}  {probe(st.path, v2)}")
    if args.step == "audio":
        return 0
    cues = [Cue(bounds[i], bounds[i + 1], t) for i, t in
            enumerate(["첫 번째 자막입니다", "짧은 클립은 반복돼요", "Final subtitle 테스트!"][: len(clips)])]
    (out / "subtitles.srt").write_text(to_srt(cues), encoding="utf-8")
    ass = out / "subtitles.ass"
    ass.write_text(to_ass(cues, font=args.font), encoding="utf-8")
    v3 = comp.burn_subtitles(v2, ass, out / "03_final.mp4")
    print(f"[3] 자막 번인: {v3}  {probe(st.path, v3)}")
    print(f"ffmpeg 로그: {out / 'logs'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
