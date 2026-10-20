from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from newsshorts.log import log

# Rough Korean narration speed used when real TTS timing isn't available.
CHARS_PER_SECOND = 7.0


@dataclass
class Cue:
    text: str
    start: float
    end: float


def _estimate_cues(sentences: list[str], gap: float = 0.25) -> list[Cue]:
    cues, t = [], 0.0
    for s in sentences:
        dur = max(1.2, len(s) / CHARS_PER_SECOND)
        cues.append(Cue(s, t, t + dur))
        t += dur + gap
    return cues


async def _edge_tts(text: str, voice: str, rate: str, out_mp3: Path) -> list[tuple[str, float, float]]:
    import edge_tts

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="SentenceBoundary", proxy=proxy)
    bounds = []
    with open(out_mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                start = chunk["offset"] / 1e7  # 100ns units
                bounds.append((chunk["text"], start, start + chunk["duration"] / 1e7))
    return bounds


def _align(sentences: list[str], bounds: list[tuple[str, float, float]], total: float) -> list[Cue]:
    """Map our sentences onto TTS sentence boundaries.

    The TTS engine may split differently from our list, so when counts don't
    match we spread our sentences proportionally to their length over the audio.
    """
    if len(bounds) == len(sentences):
        return [Cue(s, b[1], b[2]) for s, b in zip(sentences, bounds)]
    weights = [max(1, len(s)) for s in sentences]
    scale = total / sum(weights)
    cues, t = [], 0.0
    for s, w in zip(sentences, weights):
        cues.append(Cue(s, t, t + w * scale))
        t += w * scale
    return cues


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _silent_audio(seconds: float, out: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", str(out)],
        check=True,
    )


def synthesize(sentences: list[str], voice: str, rate: str, out_mp3: Path, use_tts: bool = True) -> list[Cue]:
    """Write narration to out_mp3 and return subtitle cues.

    Falls back to a silent track with estimated timing if TTS is unreachable,
    so a draft (for timing/layout review) is still produced.
    """
    if use_tts:
        try:
            bounds = asyncio.run(_edge_tts(" ".join(sentences), voice, rate, out_mp3))
            return _align(sentences, bounds, audio_duration(out_mp3))
        except Exception as exc:
            log(f"[warn] TTS failed, using silent track: {exc}")
    cues = _estimate_cues(sentences)
    _silent_audio(cues[-1].end + 0.5, out_mp3)
    return cues
