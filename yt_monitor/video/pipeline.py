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

from ..generator import has_hangul, tts_lines
from ..report import safe_name
from ..upload_meta import UploadMeta, fallback_meta, generate_upload_meta, render_upload_text
from .compose import BACKGROUND_COLORS, Composer, Shot, concat_wavs, split_frames
from .ffmpeg import Cancelled, FFmpegError, FFmpegNotFound, FFmpegRunner, check_ffmpeg
from .pexels import Clip, PexelsAuthError, PexelsClient, PexelsError
from .scenes import Scene, chars_per_second, extract_keywords, split_scenes
from .subtitles import Cue, ass_style_from_config, cues_for_scene, to_ass, to_srt
from .bgm import MOOD_BGM, bgm_credit, ensure_bgm_dirs, pick_bgm, recommend_text
from .tts import DEFAULT_MOOD, MOODS, EdgeTTS, PlaceholderTTS, save_words, voice_for_mood

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
    mood: str = ""
    voice: str = ""
    upload_path: Path | None = None     # <파일명>_업로드정보.txt
    bgm: str = ""                       # 사용한 배경음악 파일 이름
    upload_title: str = ""              # 추천 제목


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

    def __init__(self, cfg, *, pexels=None, pixabay=None, tts=None, ollama=None, comfy=None,
                 offline: bool = False):
        self.cfg = cfg
        self.v = cfg.video
        self.offline = offline
        self._pexels = pexels
        self._pixabay = pixabay
        self._tts = tts
        self._ollama = ollama
        self._comfy = comfy
        self._ollama_used = None

    # ---- 의존성 --------------------------------------------------------------------
    def _make_stock(self, warn) -> list:
        """스톡 영상 소스 목록 (Pexels, Pixabay 중 키가 있는 것)."""
        if self.offline or self._pexels is not None or self._pixabay is not None:
            return [c for c in (self._pexels, self._pixabay) if c is not None]
        out = []
        if self.cfg.secret("pexels", "api_key", required=False):
            out.append(PexelsClient.from_config(self.cfg))
        if self.cfg.secret("pixabay", "api_key", required=False):
            from .pixabay import PixabayClient

            out.append(PixabayClient.from_config(self.cfg))
        if not out:
            warn("Pexels/Pixabay API 키가 없어 스톡 영상 대신 단색 배경을 사용합니다. (설정에서 키를 넣으세요)")
        return out

    def _make_comfy(self):
        if self._comfy is not None:
            return self._comfy
        if self.offline or not self.cfg.raw.get("ai_images", {}).get("enabled"):
            return None
        from .comfyui import ComfyClient

        return ComfyClient.from_config(self.cfg.ai_images)

    def _make_tts(self, voice: str | None, mood: str, status):
        """직접 고른 음성 > (voice_mode=auto면) 분위기별 음성 > 설정의 tts_voice."""
        if self._tts is not None:
            return self._tts
        if self.offline:
            return PlaceholderTTS()
        if voice:
            status(f"음성: {voice} (직접 선택)")
            return EdgeTTS.from_config(self.v, voice)
        if str(self.v.get("voice_mode", "auto")) == "auto":
            pick = voice_for_mood(mood, self.cfg.language, self.v.get("mood_voices"))
            status(f"분위기: {mood} ({MOODS.get(mood, '')}) → 음성 {pick['voice']}, "
                   f"속도 {pick['rate']}, 음높이 {pick['pitch']}")
            return EdgeTTS.from_config(self.v, pick["voice"], rate=pick["rate"], pitch=pick["pitch"])
        return EdgeTTS.from_config(self.v)

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
        self._ollama_used = client
        return client

    # ---- 실행 ---------------------------------------------------------------------
    def run(self, script: str, title: str = "", *, voice: str | None = None, hook_text: str | None = None,
            output_path: Path | None = None, now: datetime | None = None, upload_meta=None,
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
        lang = self.cfg.language
        scenes = split_scenes(script, float(self.v.get("min_scene_seconds", 2.5)),
                              int(self.v.get("max_scene_chars", 90)), lang)
        if not scenes:
            if lang == "en" and has_hangul(script):
                raise VideoError("영어 모드인데 대본에 영어 문장이 없습니다. 영어 대본을 넣거나 "
                                 "설정에서 콘텐츠 언어를 '한국'으로 바꾸세요.")
            raise VideoError("대본에서 읽을 문장을 찾지 못했습니다. 대본을 입력하세요.")
        dropped = [ln for ln in tts_lines(script) if has_hangul(ln)] if lang == "en" else []
        if dropped:
            status(f"영어 모드라 한국어 문장 {len(dropped)}개는 읽지 않고 뺐습니다: " + " / ".join(dropped[:3]))
        status(f"씬 {len(scenes)}개로 나눴습니다.")
        o = self.cfg.ollama
        ollama = self._make_ollama(status)
        meta: dict = {}
        extract_keywords(scenes, client=ollama, prompts_dir=self.cfg.prompts_dir, meta=meta,
                         title=title, per_scene=int(self.v.get("keywords_per_scene", 3)),
                         options={"num_ctx": o.get("num_ctx", 8192)}, cancel=cancel, on_status=status)
        for s in scenes:
            status(f"  씬 {s.index}: {', '.join(s.keywords) or '-'}")
        upload = self._upload_meta(upload_meta, scenes, title, ollama, status, cancel)

        # 2) 스톡 영상 · AI 이미지 -------------------------------------------------------------
        comfy = self._make_comfy()
        step(2, "스톡 영상 검색 · 다운로드 중…" + (" (+ AI 이미지)" if comfy is not None else ""))
        stock = self._make_stock(warn)
        clip_max = float(self.v.get("clip_max_seconds", 2.5))
        scene_clips: dict[int, list[Clip]] = {}
        used: set[tuple[str, int]] = set()
        if stock:
            cache = self.cfg.video_cache_dir
            for s in scenes:
                check_cancel()
                est = len(s.text) / chars_per_second(s.text)
                want = min(MAX_CLIPS_PER_SCENE, max(1, math.ceil(est / clip_max)))
                # 씬마다 시작 소스를 번갈아 → Pexels·Pixabay 영상이 고루 섞이게
                order = stock[(s.index - 1) % len(stock):] + stock[:(s.index - 1) % len(stock)]
                picked: list[tuple[object, Clip]] = []
                for kw in s.keywords:
                    for provider in order:
                        if len(picked) >= want:
                            break
                        try:
                            results = provider.search(kw)
                        except PexelsAuthError:
                            raise
                        except PexelsError as exc:
                            warn(f"'{kw}' 검색 실패: {exc}")
                            continue
                        fresh = [c for c in results if c.key not in used] or results[:1]
                        if fresh:
                            picked.append((provider, fresh[0]))
                            used.add(fresh[0].key)
                    if len(picked) >= want:
                        break
                good = []
                for provider, c in picked:
                    check_cancel()
                    try:
                        provider.download(c, cache, cancel)
                        good.append(c)
                    except InterruptedError:
                        raise Cancelled("사용자가 영상 생성을 취소했습니다.") from None
                    except PexelsError as exc:
                        warn(str(exc))
                scene_clips[s.index] = good
                srcs = ", ".join(sorted({c.source for c in good}))
                status(f"  씬 {s.index}: 영상 {len(good)}개" + (f" ({srcs})" if good else " → 단색 배경"))
            status("요청 수: " + ", ".join(f"{type(p).__name__.replace('Client', '')} "
                                            f"{getattr(p, 'requests_made', 0)}회" for p in stock))
        scene_images = self._ai_images(comfy, scenes, scene_clips, work, status, warn, cancel) if comfy else {}

        # 3) TTS -------------------------------------------------------------------------
        step(3, "TTS 음성 생성 중…")
        tts = self._make_tts(voice, meta.get("mood", ""), status)
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
        mood = meta.get("mood") if meta.get("mood") in MOODS else DEFAULT_MOOD
        audio, bgm_track = self._add_bgm(comp, narration, total, mood, status, warn)

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
        if hook_text is None:
            hook_text = title
            if lang == "en" and has_hangul(title):     # 영어 영상에 한국어 제목이 뜨지 않게 → 첫 문장
                hook_text = scenes[0].text.split(". ")[0]
        hook_sec = min(float(self.v.get("hook_seconds", 2.5) or 0), total)
        hook = Cue(0.0, hook_sec, hook_text) if hook_text and hook_text.strip() and hook_sec > 0 else None
        ass = work / "subtitles.ass"
        ass.write_text(to_ass(cues, hook=hook, **ass_style_from_config(self.v)), encoding="utf-8")
        if hook:
            status(f"첫 화면 훅 문구 ({hook_sec:.1f}초): {hook.text}")
        status(f"자막 {len(cues)}개")

        # 5) 클립 이어붙이기 + 음성 --------------------------------------------------------
        step(5, "클립 이어붙이기 · 음성 삽입 중…")
        shots: list[Shot] = []
        for s, frames in zip(scenes, split_frames(bounds, fps, clip_max)):
            # AI 이미지가 있으면 그 씬의 첫 컷으로, 나머지는 스톡 영상
            sources: list = ([scene_images[s.index]] if s.index in scene_images else []) + \
                (scene_clips.get(s.index) or [])
            color = BACKGROUND_COLORS[(s.index - 1) % len(BACKGROUND_COLORS)]
            for k, n in enumerate(frames):
                zoom_out = len(shots) % 2 == 1      # 컷마다 줌인/줌아웃 번갈아
                if not sources:
                    shots.append(Shot(n, None, color=color))
                    continue
                src = sources[k % len(sources)]
                if isinstance(src, Path):
                    shots.append(Shot(n, src, color=color, zoom_out=zoom_out))
                    continue
                # 같은 클립을 다시 쓰면 뒷부분부터 보여준다
                reuse = k // len(sources)
                seek = (reuse * n / fps) % src.duration if reuse and src.duration > n / fps else 0.0
                shots.append(Shot(n, src.path, seek=seek, color=color, zoom_out=zoom_out))
        self._write_debug(work, scenes, scene_clips, durations, shots)
        shot_paths = comp.prepare_shots(shots, lambda i, n: (check_cancel(),
                                                             status(f"  클립 맞추기 {i}/{n}")))
        v1 = comp.concat(shot_paths)
        v2 = comp.add_audio(v1, audio, total)

        # 6) 자막 번인 · 저장 -----------------------------------------------------------------
        step(6, "자막 입히기 · 저장 중…")
        comp.burn_subtitles(v2, ass, final)
        srt = final.with_suffix(".srt")
        shutil.copyfile(srt_work, srt)
        credits = self._write_credits(final, scenes, scene_clips, bgm_credit(bgm_track) if bgm_track else "")
        upload_path = None
        if upload is not None:
            upload_path = final.with_name(final.stem + "_업로드정보.txt")
            credit_text = credits.read_text(encoding="utf-8") if credits else ""
            bgm_note = (f"사용한 곡: {bgm_track.name}" if bgm_track else
                        f"(bgm/{mood} 폴더가 비어 있어 목소리만 넣었습니다)") + "\n   추천: " + recommend_text(mood)
            upload_path.write_text(render_upload_text(upload, credit_text, bgm_note) + "\n", encoding="utf-8")
            status(f"업로드 정보 저장: {upload_path.name} (추천 제목: {upload.title})")
        if not self.v.get("keep_work_files", True):
            shutil.rmtree(work, ignore_errors=True)
            work_kept = None
        else:
            work_kept = work
        status(f"완료: {final} ({total:.1f}초)")
        return VideoResult(final, srt, credits, work_kept, total, scenes, warnings,
                           mood=meta.get("mood", ""), voice=str(getattr(tts, "voice", "")),
                           upload_path=upload_path, upload_title=upload.title if upload else "",
                           bgm=bgm_track.name if bgm_track else "")

    def _add_bgm(self, comp, narration: Path, total: float, mood: str, status, warn) -> tuple[Path, Path | None]:
        """분위기에 맞는 배경음악을 bgm/ 폴더에서 골라 섞는다. 곡이 없거나 실패하면 목소리만."""
        if not self.v.get("bgm_enabled", True):
            return narration, None
        root = self.cfg.bgm_dir
        try:
            ensure_bgm_dirs(root)
        except OSError:
            pass
        track = pick_bgm(root, mood)
        if track is None:
            status(f"배경음악 없음 → {root / mood} 폴더에 무료 곡을 넣으면 자동으로 깔립니다. "
                   f"추천: {MOOD_BGM.get(mood, MOOD_BGM['energetic'])['ko']}")
            return narration, None
        status(f"배경음악 ({mood}): {track.name}")
        try:
            return comp.mix_bgm(narration, track, total, volume=float(self.v.get("bgm_volume", 0.15)),
                                duck=bool(self.v.get("bgm_duck", True))), track
        except FFmpegError as exc:
            warn(f"배경음악 '{track.name}'을(를) 넣지 못해 목소리만 넣었습니다: {str(exc).splitlines()[0]}")
            return narration, None

    def _upload_meta(self, given, scenes, title, ollama, status, cancel) -> UploadMeta | None:
        """주제/대본 단계에서 만든 업로드 정보가 있으면 그대로, 없으면 여기서 만든다."""
        if isinstance(given, UploadMeta):
            return given
        if isinstance(given, dict):
            return UploadMeta.from_dict(given)
        if not self.v.get("upload_meta", True):
            return None
        script = "\n".join(s.text for s in scenes)
        if ollama is None:
            return fallback_meta(title, None, [], self.cfg.language, script)
        status("업로드 정보(제목 후보 · 카테고리 · 해시태그) 만드는 중…")
        o = self.cfg.ollama
        return generate_upload_meta(ollama, self.cfg.prompts_dir, lang=self.cfg.language, title=title,
                                    script=script, options={"num_ctx": o.get("num_ctx", 8192)}, cancel=cancel)

    # ---- AI 이미지 -------------------------------------------------------------------
    def _ai_images(self, comfy, scenes, scene_clips, work: Path, status, warn, cancel) -> dict[int, Path]:
        """mix: 첫 씬(훅) + 스톡 영상을 못 찾은 씬 / all: 모든 씬 → ComfyUI로 세로 이미지 생성."""
        mode = str(self.cfg.raw.get("ai_images", {}).get("mode", "mix"))
        targets = [s for s in scenes if mode == "all" or s.index == 1 or not scene_clips.get(s.index)]
        if not targets:
            return {}
        launcher = None
        st = comfy.status()
        ai = self.cfg.ai_images
        if not st.server_up and ai.get("auto_start", True):
            from .comfy_launcher import ComfyLauncher

            launcher = ComfyLauncher.from_config(ai, work / "logs")
            if launcher is not None:
                if self._ollama_used is not None:
                    self._ollama_used.unload()
                status(f"ComfyUI 자동 실행 중… ({launcher.comfy_dir}) — 처음 켤 때 1~2분 걸립니다")
                try:
                    started = launcher.start(cancel, status)
                except InterruptedError:
                    raise Cancelled("사용자가 영상 생성을 취소했습니다.") from None
                if not started:
                    warn(f"ComfyUI를 자동으로 켜지 못했습니다. 로그: {launcher.log_path}")
                    launcher = None
                st = comfy.status()
        if not st.ok:
            if launcher is not None and ai.get("auto_stop", True):
                launcher.stop()
            warn("AI 이미지를 건너뜁니다: " + st.message.splitlines()[0])
            return {}
        if self._ollama_used is not None:
            self._ollama_used.unload()        # 그래픽카드 메모리를 이미지 생성에 넘겨준다
        out: dict[int, Path] = {}
        from .comfyui import ComfyError

        try:
            for n, s in enumerate(targets, 1):
                if cancel is not None and cancel.is_set():
                    raise Cancelled("사용자가 영상 생성을 취소했습니다.")
                status(f"  AI 이미지 {n}/{len(targets)} (씬 {s.index}): {s.image_prompt}")
                try:
                    out[s.index] = comfy.generate(s.image_prompt, work / "ai" / f"scene_{s.index:03d}.png",
                                                  cancel=cancel)
                except InterruptedError:
                    raise Cancelled("사용자가 영상 생성을 취소했습니다.") from None
                except ComfyError as exc:
                    warn(f"씬 {s.index} AI 이미지 실패: {exc}")
                    if not out:                    # 첫 장부터 실패하면 나머지도 실패할 가능성이 높다
                        break
        finally:
            comfy.free_memory()
            if launcher is not None and ai.get("auto_stop", True):
                launcher.stop()              # 프로그램이 켠 ComfyUI만 끈다 → 그래픽카드 메모리 완전히 반환
                status("ComfyUI 종료 (자동)")
        status(f"AI 이미지 {len(out)}장 생성 ({st.checkpoint})")
        return out

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
    def _write_credits(final: Path, scenes, scene_clips, music: str = "") -> Path | None:
        """영상 설명란에 붙여넣을 출처 목록 (영어권 시청자 기준으로 영어)."""
        seen, lines, sources = set(), [], set()
        for s in scenes:
            for c in scene_clips.get(s.index, []):
                if c.key not in seen:
                    seen.add(c.key)
                    sources.add(c.source)
                    lines.append(f"- {c.credit}")
        if not lines and not music:
            return None
        sites = {"pexels": "Pexels (pexels.com)", "pixabay": "Pixabay (pixabay.com)"}
        text = ""
        if lines:
            text = ("Stock footage: " + ", ".join(sites.get(x, x) for x in sorted(sources)) + "\n"
                    + "\n".join(lines) + "\n")
        if music:
            text += ("\n" if text else "") + "Music: " + music + "\n"
        path = final.with_name(final.stem + "_출처.txt")
        path.write_text(text, encoding="utf-8")
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
    if res.upload_path:
        print(f"📋 {res.upload_path}")
    if res.work_dir:
        print(f"🔧 중간 파일 / ffmpeg 로그: {res.work_dir}")
    for w in res.warnings:
        print(f"⚠ {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
