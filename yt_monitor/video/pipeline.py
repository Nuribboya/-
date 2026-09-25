"""대본 → 쇼츠 mp4 전체 흐름 (6단계).

    1/6 씬 분리 · 키워드 추출   (Ollama, 실패 시 대본 단어)
    2/6 스톡 영상 검색 · 다운로드 (Pexels, 못 찾으면 단색 배경)
    3/6 TTS 음성 생성            (edge-tts, 씬마다 따로 합성해 씬 길이를 정확히 앎)
    4/6 자막 생성                (단어 타임스탬프 → SRT + ASS)
    5/6 클립 이어붙이기 + 음성    (ffmpeg)
    6/6 자막 번인 · 저장          (ffmpeg) → outputs/YYYY-MM-DD/<제목>.mp4

결과: <제목>.mp4, <제목>.srt, <제목>_출처.txt (+ 설정에 따라 <제목>_work/ 중간 파일과 ffmpeg 로그)

단독 실행:
    python -m yt_monitor.video.pipeline 대본.txt --title "편의점 꿀조합"
    python -m yt_monitor.video.pipeline 대본.txt --offline     # 인터넷/API 키 없이 (무음 + 단색 배경) 합성만 확인
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..report import safe_name
from .compose import BACKGROUND_COLORS, Composer, Shot, concat_wavs, split_frames
from .ffmpeg import Cancelled, FFmpegNotFound, FFmpegRunner, check_ffmpeg
from .pexels import Clip, PexelsAuthError, PexelsClient, PexelsError
from .scenes import Scene, chars_per_second, extract_keywords, split_scenes
from .subtitles import ass_style_from_config, cues_for_scene, to_ass, to_srt
from .tts import EdgeTTS, PlaceholderTTS, save_words

log = logging.getLogger(__name__)

TOTAL_STEPS = 6
MAX_CLIPS_PER_SCENE = 3


class VideoError(RuntimeError):
    pass


@dataclass
class VideoResult:
    video_path: Path
    srt_path: Path
    credits_path: Path | None
    work_dir: Path | None
    duration: float
    scenes: list[Scene]
    warnings: list[str] = field(default_factory=list)


def output_path_for(outputs_dir: Path, title: str, now: datetime, tz: ZoneInfo) -> Path:
    """outputs/YYYY-MM-DD/<제목>.mp4 (이미 있으면 <제목>_HHMMSS.mp4)."""
    local = now.astimezone(tz)
    day_dir = Path(outputs_dir) / local.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    name = safe_name(title or "영상")
    path = day_dir / f"{name}.mp4"
    if path.exists():
        path = day_dir / f"{name}_{local:%H%M%S}.mp4"
    return path


class VideoPipeline:
    """cfg(Config)의 video/pexels/ollama 설정으로 영상을 만든다.

    pexels / tts / ollama 를 넘기면 그걸 쓴다 (테스트용). offline=True 면 네트워크를 전혀 쓰지 않는다.
    """

    def __init__(self, cfg, *, pexels=None, tts=None, ollama=None, offline: bool = False):
        self.cfg = cfg
        self.v = cfg.video
        self.offline = offline
        self._pexels = pexels
        self._tts = tts
        self._ollama = ollama

    # ---- 의존성 --------------------------------------------------------------------
    def _make_pexels(self, warn):
        if self._pexels is not None or self.offline:
            return self._pexels
        client = PexelsClient.from_config(self.cfg) if self.cfg.secret(
            "pexels", "api_key", required=False) else None
        if client is None:
            warn("Pexels API 키가 없어 스톡 영상 대신 단색 배경을 사용합니다. (설정에서 키를 넣으세요)")
        return client

    def _make_tts(self, voice):
        if self._tts is not None:
            return self._tts
        return PlaceholderTTS() if self.offline else EdgeTTS.from_config(self.v, voice)

    def _make_ollama(self, status):
        if self._ollama is not None or self.offline:
            return self._ollama
        o = self.cfg.ollama
        if not o.get("enabled", True):
            return None
        from ..ollama_client import OllamaClient

        client = OllamaClient(o["host"], o["model"], timeout=o.get("timeout_sec", 900))
        st = client.status()
        if not st.ok:
            status(f"Ollama 사용 불가 → 대본 단어로 검색합니다 ({st.message.splitlines()[0]})")
            return None
        return client

    # ---- 실행 ---------------------------------------------------------------------
    def run(self, script: str, title: str = "", *, voice: str | None = None,
            output_path: Path | None = None, now: datetime | None = None,
            on_progress: Callable[[int, int, str], None] | None = None,
            on_status: Callable[[str], None] | None = None,
            cancel: threading.Event | None = None) -> VideoResult:
        warnings: list[str] = []

        def status(msg: str):
            log.info(msg)
            if on_status:
                on_status(msg)

        def warn(msg: str):
            warnings.append(msg)
            status("⚠ " + msg)

        def step(n: int, msg: str):
            if cancel is not None and cancel.is_set():
                raise Cancelled("사용자가 영상 생성을 취소했습니다.")
            log.info("[%d/%d] %s", n, TOTAL_STEPS, msg)
            if on_progress:
                on_progress(n, TOTAL_STEPS, msg)

        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise Cancelled("사용자가 영상 생성을 취소했습니다.")

        ff = check_ffmpeg(self.v.get("ffmpeg_path"))
        if not ff.ok:
            raise FFmpegNotFound(ff.message)
        tz = ZoneInfo(self.cfg.schedule.get("timezone") or "Asia/Seoul")
        now = now or datetime.now(tz)
        final = Path(output_path) if output_path else output_path_for(self.cfg.outputs_dir, title, now, tz)
        final.parent.mkdir(parents=True, exist_ok=True)
        work = final.with_name(final.stem + "_work")
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        runner = FFmpegRunner(ff.path, work / "logs", cancel)
        comp = Composer(runner, work, self.v)
        fps = comp.fps
        status(f"작업 폴더: {work}")

        # 1) 씬 분리 · 키워드 --------------------------------------------------------
        step(1, "씬 분리 · 키워드 추출 중…")
        scenes = split_scenes(script, float(self.v.get("min_scene_seconds", 2.5)),
                              int(self.v.get("max_scene_chars", 90)))
        if not scenes:
            raise VideoError("대본에서 읽을 문장을 찾지 못했습니다. 대본을 입력하세요.")
        status(f"씬 {len(scenes)}개로 나눴습니다.")
        o = self.cfg.ollama
        extract_keywords(scenes, client=self._make_ollama(status), prompts_dir=self.cfg.prompts_dir,
                         title=title, per_scene=int(self.v.get("keywords_per_scene", 3)),
                         options={"num_ctx": o.get("num_ctx", 8192)}, cancel=cancel, on_status=status)
        for s in scenes:
            status(f"  씬 {s.index}: {', '.join(s.keywords) or '-'}")

        # 2) 스톡 영상 ------------------------------------------------------------------
        step(2, "스톡 영상 검색 · 다운로드 중…")
        pexels = self._make_pexels(warn)
        clip_max = float(self.v.get("clip_max_seconds", 4.0))
        scene_clips: dict[int, list[Clip]] = {}
        used: set[int] = set()
        if pexels is not None:
            cache = self.cfg.video_cache_dir
            for s in scenes:
                check_cancel()
                est = len(s.text) / chars_per_second(s.text)
                want = min(MAX_CLIPS_PER_SCENE, max(1, math.ceil(est / clip_max)))
                picked: list[Clip] = []
                for kw in s.keywords:
                    if len(picked) >= want:
                        break
                    try:
                        results = pexels.search(kw)
                    except PexelsAuthError:
                        raise
                    except PexelsError as exc:
                        warn(f"'{kw}' 검색 실패: {exc}")
                        continue
                    fresh = [c for c in results if c.video_id not in used] or results
                    for c in fresh[: want - len(picked)]:
                        picked.append(c)
                        used.add(c.video_id)
                good = []
                for c in picked:
                    check_cancel()
                    try:
                        pexels.download(c, cache, cancel)
                        good.append(c)
                    except InterruptedError:
                        raise Cancelled("사용자가 영상 생성을 취소했습니다.") from None
                    except PexelsError as exc:
                        warn(str(exc))
                scene_clips[s.index] = good
                status(f"  씬 {s.index}: 영상 {len(good)}개" + (" → 단색 배경" if not good else ""))
            status(f"Pexels 요청 {getattr(pexels, 'requests_made', 0)}회")

        # 3) TTS -------------------------------------------------------------------------
        step(3, "TTS 음성 생성 중…")
        tts = self._make_tts(voice)
        tts_dir = work / "tts"
        tts_dir.mkdir()
        wavs, scene_words, durations = [], [], []
        for s in scenes:
            check_cancel()
            res = tts.synthesize(s.text, tts_dir / f"scene_{s.index:03d}.mp3")
            save_words(res.words, tts_dir / f"scene_{s.index:03d}_words.json")
            wav = tts_dir / f"scene_{s.index:03d}_pcm.wav"
            durations.append(comp.decode_audio(res.audio_path, wav))
            wavs.append(wav)
            scene_words.append(res.words)
            status(f"  씬 {s.index}: {durations[-1]:.1f}초")
        narration = work / "narration.wav"
        total = concat_wavs(wavs, narration)
        status(f"전체 음성 {total:.1f}초 ({getattr(tts, 'voice', '')})")

        # 4) 자막 -------------------------------------------------------------------------
        step(4, "자막 생성 중…")
        bounds = [0.0]
        for d in durations:
            bounds.append(bounds[-1] + d)
        max_chars = int(self.v.get("subtitle_max_chars", 14))
        cues = []
        for s, words, a, b in zip(scenes, scene_words, bounds, bounds[1:]):
            cues += cues_for_scene(s.text, words, a, b, max_chars)
        srt_work = work / "subtitles.srt"
        srt_work.write_text(to_srt(cues), encoding="utf-8")
        ass = work / "subtitles.ass"
        ass.write_text(to_ass(cues, **ass_style_from_config(self.v)), encoding="utf-8")
        status(f"자막 {len(cues)}개")

        # 5) 클립 이어붙이기 + 음성 --------------------------------------------------------
        step(5, "클립 이어붙이기 · 음성 삽입 중…")
        shots: list[Shot] = []
        for s, frames in zip(scenes, split_frames(bounds, fps, clip_max)):
            clips = scene_clips.get(s.index) or []
            color = BACKGROUND_COLORS[(s.index - 1) % len(BACKGROUND_COLORS)]
            for k, n in enumerate(frames):
                if not clips:
                    shots.append(Shot(n, None, color=color))
                    continue
                c = clips[k % len(clips)]
                # 같은 클립을 다시 쓰면 뒷부분부터 보여준다
                reuse = k // len(clips)
                seek = (reuse * n / fps) % c.duration if reuse and c.duration > n / fps else 0.0
                shots.append(Shot(n, c.path, seek=seek, color=color))
        self._write_debug(work, scenes, scene_clips, durations, shots)
        shot_paths = comp.prepare_shots(shots, lambda i, n: (check_cancel(),
                                                             status(f"  클립 맞추기 {i}/{n}")))
        v1 = comp.concat(shot_paths)
        v2 = comp.add_audio(v1, narration, total)

        # 6) 자막 번인 · 저장 -----------------------------------------------------------------
        step(6, "자막 입히기 · 저장 중…")
        comp.burn_subtitles(v2, ass, final)
        srt = final.with_suffix(".srt")
        shutil.copyfile(srt_work, srt)
        credits = self._write_credits(final, scenes, scene_clips)
        if not self.v.get("keep_work_files", True):
            shutil.rmtree(work, ignore_errors=True)
            work_kept = None
        else:
            work_kept = work
        status(f"완료: {final} ({total:.1f}초)")
        return VideoResult(final, srt, credits, work_kept, total, scenes, warnings)

    # ---- 기록 ------------------------------------------------------------------------
    @staticmethod
    def _write_debug(work: Path, scenes, scene_clips, durations, shots) -> None:
        info = {
            "scenes": [{**asdict(s), "duration": d,
                        "clips": [c.page_url or str(c.path) for c in scene_clips.get(s.index, [])]}
                       for s, d in zip(scenes, durations)],
            "shots": [{"frames": sh.frames, "source": str(sh.source) if sh.source else None,
                       "seek": sh.seek} for sh in shots],
        }
        (work / "scenes.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def _write_credits(final: Path, scenes, scene_clips) -> Path | None:
        seen, lines = set(), []
        for s in scenes:
            for c in scene_clips.get(s.index, []):
                if c.video_id not in seen:
                    seen.add(c.video_id)
                    lines.append(f"- {c.credit}")
        if not lines:
            return None
        path = final.with_name(final.stem + "_출처.txt")
        path.write_text("영상 출처 (Pexels, https://www.pexels.com)\n" + "\n".join(lines) + "\n",
                        encoding="utf-8")
        return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    from ..config import Config, read_raw
    from ..paths import default_config_path

    parser = argparse.ArgumentParser(description="대본 → 쇼츠 영상 (전체 파이프라인)")
    parser.add_argument("script", help="대본 텍스트 파일 (.txt / .md)")
    parser.add_argument("--title", help="영상 제목 = 파일 이름 (생략하면 대본 파일 이름)")
    parser.add_argument("--config", help="config.yaml 경로")
    parser.add_argument("--voice", help="edge-tts 음성 (예: ko-KR-InJoonNeural)")
    parser.add_argument("--offline", action="store_true",
                        help="인터넷 없이: 무음 + 단색 배경 + 간이 키워드 (ffmpeg 합성 확인용)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    path = Path(args.config) if args.config else default_config_path()
    try:
        from dotenv import load_dotenv

        load_dotenv(path.parent / ".env", override=False)
    except ImportError:
        pass
    cfg = Config(read_raw(path), path.parent.resolve(), path=path)
    script_file = Path(args.script)
    res = VideoPipeline(cfg, offline=args.offline).run(
        script_file.read_text(encoding="utf-8"), args.title or script_file.stem, voice=args.voice)
    print(f"\n🎬 {res.video_path}\n📝 {res.srt_path}" + (f"\n📄 {res.credits_path}" if res.credits_path else ""))
    if res.work_dir:
        print(f"🔧 중간 파일 / ffmpeg 로그: {res.work_dir}")
    for w in res.warnings:
        print(f"⚠ {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
