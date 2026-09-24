"""edge-tts (Microsoft Edge 온라인 TTS) — 무료, API 키 없음, 한국어/영어 지원.

텍스트 → mp3 + 단어별 타임스탬프(WordBoundary). 타임스탬프로 자막 싱크를 맞춘다.

한국어 음성: ko-KR-SunHiNeural(여), ko-KR-InJoonNeural(남), ko-KR-HyunsuMultilingualNeural(남)
영어 음성:   en-US-JennyNeural(여), en-US-GuyNeural(남), en-US-AriaNeural(여)
전체 목록:   edge-tts --list-voices

단독 테스트 (텍스트 → 음성 파일 + 단어 타이밍 + SRT):
    python -m yt_monitor.video.tts "안녕하세요. 오늘은 자취 요리를 알아볼게요." --out samples/tts
    python -m yt_monitor.video.tts --file 대본.txt --voice ko-KR-InJoonNeural --rate +10%
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

VOICES = {
    "ko-KR-SunHiNeural": "한국어 여성 (선희)",
    "ko-KR-InJoonNeural": "한국어 남성 (인준)",
    "ko-KR-HyunsuMultilingualNeural": "한국어 남성 (현수)",
    "en-US-JennyNeural": "영어 여성 (Jenny)",
    "en-US-GuyNeural": "영어 남성 (Guy)",
    "en-US-AriaNeural": "영어 여성 (Aria)",
}
TICKS = 10_000_000  # edge-tts 시간 단위: 100ns


class TTSError(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    start: float   # 초
    end: float


@dataclass
class TTSResult:
    audio_path: Path
    words: list[Word] = field(default_factory=list)


class EdgeTTS:
    def __init__(self, voice: str = "ko-KR-SunHiNeural", rate: str = "+0%", volume: str = "+0%",
                 pitch: str = "+0Hz", proxy: str | None = None, retries: int = 3,
                 retry_delay: float = 1.5, timeout: float = 120):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.proxy = proxy or None
        self.retries = retries
        self.retry_delay = retry_delay
        self.timeout = timeout

    @classmethod
    def from_config(cls, video_cfg: dict, voice: str | None = None) -> "EdgeTTS":
        return cls(voice or video_cfg.get("tts_voice") or "ko-KR-SunHiNeural",
                   rate=str(video_cfg.get("tts_rate") or "+0%"),
                   volume=str(video_cfg.get("tts_volume") or "+0%"),
                   pitch=str(video_cfg.get("tts_pitch") or "+0Hz"),
                   proxy=video_cfg.get("tts_proxy") or None)

    def _communicate(self, text: str):
        import edge_tts

        return edge_tts.Communicate(text, self.voice, rate=self.rate, volume=self.volume,
                                    pitch=self.pitch, boundary="WordBoundary", proxy=self.proxy)

    def _stream(self, text: str):
        """청크 목록을 받아온다.

        edge-tts의 stream_sync()는 네트워크 오류가 나면 예외를 삼키고 영원히 기다리는 문제가 있어서
        (내부 스레드의 예외를 확인하지 않음) async stream()을 직접 돌리고 전체 시간 제한을 건다.
        """
        communicate = self._communicate(text)
        if not hasattr(communicate, "stream"):          # 테스트용 가짜 객체
            return list(communicate.stream_sync())

        async def collect():
            return [chunk async for chunk in communicate.stream()]

        return asyncio.run(asyncio.wait_for(collect(), timeout=self.timeout))

    def synthesize(self, text: str, out_path: Path) -> TTSResult:
        """text → out_path(mp3). 네트워크 오류는 retries 번까지 다시 시도."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                words: list[Word] = []
                with open(out_path, "wb") as f:
                    for chunk in self._stream(text):
                        if chunk["type"] == "audio":
                            f.write(chunk["data"])
                        elif chunk["type"] == "WordBoundary":
                            start = chunk["offset"] / TICKS
                            words.append(Word(chunk["text"], start, start + chunk["duration"] / TICKS))
                if out_path.stat().st_size == 0:
                    raise TTSError("음성 데이터가 비어 있습니다.")
                return TTSResult(out_path, words)
            except ImportError as exc:
                raise TTSError("edge-tts 패키지가 없습니다: pip install edge-tts") from exc
            except Exception as exc:  # edge-tts는 aiohttp/websocket 예외를 그대로 던진다
                last_exc = exc
                log.warning("TTS 실패 (%d/%d): %s: %s", attempt + 1, self.retries, type(exc).__name__, exc)
                time.sleep(self.retry_delay * (attempt + 1))
        detail = str(last_exc) or ("응답 시간 초과" if isinstance(last_exc, asyncio.TimeoutError) else "")
        raise TTSError(
            f"edge-tts 음성 생성 실패 ({self.voice}): {type(last_exc).__name__}: {detail}\n"
            "인터넷 연결을 확인하세요. (edge-tts는 speech.platform.bing.com 에 접속합니다. "
            "회사망이면 설정의 video.tts_proxy 에 프록시를 넣어보세요)")


class PlaceholderTTS:
    """오프라인 데모용: 인터넷 없이 무음 wav + 글자 수로 추정한 단어 타이밍을 만든다.

    ffmpeg 합성/자막 싱크를 네트워크 없이 확인할 때 사용 (--offline).
    """

    voice = "offline-placeholder"

    def __init__(self, chars_per_second: float = 6.5, sample_rate: int = 24000):
        self.cps = chars_per_second
        self.sample_rate = sample_rate

    def synthesize(self, text: str, out_path: Path) -> TTSResult:
        out_path = Path(out_path).with_suffix(".wav")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        words, t = [], 0.15
        for token in text.split():
            dur = max(len(token), 1) / self.cps
            words.append(Word(token.strip(".,!?…"), t, t + dur))
            t += dur + 0.05
        total = t + 0.25
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(b"\x00\x00" * int(total * self.sample_rate))
        return TTSResult(out_path, words)


def save_words(words: list[Word], path: Path) -> Path:
    Path(path).write_text(json.dumps([asdict(w) for w in words], ensure_ascii=False, indent=1),
                          encoding="utf-8")
    return Path(path)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="edge-tts 단독 테스트")
    parser.add_argument("text", nargs="?", default="안녕하세요. 무료 TTS 테스트입니다. 자막 싱크도 함께 확인해 볼게요.")
    parser.add_argument("--file", help="텍스트 파일 (대본)")
    parser.add_argument("--voice", default="ko-KR-SunHiNeural", help=" / ".join(VOICES))
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--out", default="samples/tts")
    parser.add_argument("--offline", action="store_true", help="인터넷 없이 무음 + 추정 타이밍")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    text = " ".join(text.split())
    out = Path(args.out)
    tts = PlaceholderTTS() if args.offline else EdgeTTS(args.voice, rate=args.rate)
    res = tts.synthesize(text, out / "tts.mp3")
    save_words(res.words, out / "tts_words.json")

    from .subtitles import cues_for_scene, to_srt

    end = res.words[-1].end + 0.3 if res.words else 1.0
    (out / "tts.srt").write_text(to_srt(cues_for_scene(text, res.words, 0.0, end)), encoding="utf-8")
    print(f"음성: {res.audio_path}\n단어 타이밍: {out / 'tts_words.json'} ({len(res.words)}개)\n"
          f"자막: {out / 'tts.srt'}")
    for w in res.words[:10]:
        print(f"  {w.start:6.2f}~{w.end:6.2f}s  {w.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
