"""자막 만들기: TTS 단어 타임스탬프 → 자막 덩어리(cue) → SRT / ASS.

- SRT: 유튜브에 따로 올리거나 편집 프로그램에서 쓰는 표준 자막 파일 (결과 mp4 옆에 저장)
- ASS: 영상에 번인(burn-in)할 때 사용. 1080x1920 좌표계로 폰트 크기/여백을 픽셀 단위로 정확히 지정하고,
  쇼츠 스타일(하단 중앙, 굵은 흰 글씨, 반투명 검은 박스)을 적용한다.

자막 텍스트는 원래 대본 문장(문장부호 포함)을 짧은 덩어리로 나눈 것이고, 각 덩어리의 시작 시각은
그 안에 든 첫 단어의 TTS 타임스탬프로 정한다. 타임스탬프가 없으면 글자 수 비율로 추정한다.

단독 테스트 (대본 → 추정 타이밍 SRT/ASS):
    python -m yt_monitor.video.subtitles 대본.txt --out samples/subs
    python -m yt_monitor.video.subtitles 대본.txt --words samples/tts/tts_words.json
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .tts import Word

_SENTENCE_END = re.compile(r"[.!?…。！？]$")
_SOFT_BREAK = re.compile(r"[,，、:;]$")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def split_caption(text: str, max_chars: int = 14) -> list[tuple[str, int, int]]:
    """문장을 자막 덩어리로 나눈다 → [(덩어리, 시작 글자 위치, 끝 글자 위치)].

    띄어쓰기 단위로 max_chars 까지 채우고, 문장 끝(. ! ?)에서는 항상, 쉼표에서는 절반 이상 찼으면 끊는다.
    """
    tokens = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    chunks: list[tuple[str, int, int]] = []
    cur: list[tuple[str, int, int]] = []

    def flush():
        if cur:
            chunks.append((" ".join(t for t, _, _ in cur), cur[0][1], cur[-1][2]))
            cur.clear()

    def length() -> int:   # 공백 포함 글자 수
        return sum(len(t) for t, _, _ in cur) + len(cur) - 1 if cur else 0

    for tok in tokens:
        if cur and length() + 1 + len(tok[0]) > max_chars:
            flush()
        cur.append(tok)
        if _SENTENCE_END.search(tok[0]) or (_SOFT_BREAK.search(tok[0]) and length() >= max_chars / 2):
            flush()
    flush()
    return chunks


def display_text(chunk: str) -> str:
    """화면에 보일 글자: 끝의 마침표/쉼표는 뺀다 (물음표/느낌표는 유지)."""
    return re.sub(r"[.,，。…]+$", "", chunk).strip() or chunk


def _interp(anchors: list[tuple[int, float]], pos: int) -> float:
    """글자 위치 → 시각 (앵커 사이 선형 보간)."""
    prev = anchors[0]
    for a in anchors[1:]:
        if pos <= a[0]:
            span = a[0] - prev[0]
            return prev[1] if span <= 0 else prev[1] + (a[1] - prev[1]) * (pos - prev[0]) / span
        prev = a
    return anchors[-1][1]


def cues_for_scene(text: str, words: list[Word], offset: float, end: float,
                   max_chars: int = 14) -> list[Cue]:
    """씬 하나의 자막. words 는 씬 음성 기준(0초부터) 타임스탬프, offset 은 씬의 전체 영상 내 시작 시각."""
    chunks = split_caption(text, max_chars)
    if not chunks:
        return []
    duration = max(end - offset, 0.1)
    # 단어 타임스탬프 → 원문 글자 위치에 대응
    matched: list[tuple[int, float, float]] = []
    cursor = 0
    for w in words:
        token = w.text.strip()
        if not token:
            continue
        idx = text.find(token, cursor)
        if idx < 0:
            continue
        matched.append((idx, w.start, w.end))
        cursor = idx + len(token)
    speech_start = matched[0][1] if matched else 0.0
    speech_end = max((m[2] for m in matched), default=duration)
    speech_end = min(max(speech_end, speech_start + 0.1), duration)
    anchors = [(0, speech_start)] + [(i, s) for i, s, _ in matched] + [(len(text), speech_end)]
    anchors.sort()

    starts = []
    for _, a, b in chunks:
        inside = [s for i, s, _ in matched if a <= i < b]
        t = min(inside) if inside else _interp(anchors, a)
        starts.append(max(t, starts[-1] + 0.05 if starts else 0.0))
    cues = []
    for n, ((chunk, _, _), t) in enumerate(zip(chunks, starts)):
        t_end = starts[n + 1] if n + 1 < len(starts) else min(duration, speech_end + 0.4)
        t_end = max(t_end, t + 0.2)
        cues.append(Cue(offset + t, min(offset + t_end, end), display_text(chunk)))
    return cues


# ---- 파일 형식 -----------------------------------------------------------------------

def _srt_time(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    return "\n".join(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n"
                     for i, c in enumerate(cues, 1))


def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def hook_display(text: str) -> str:
    """첫 화면 훅 문구: 영어는 대문자로 (쇼츠에서 흔한 스타일), 끝 마침표 제거."""
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return text.upper() if text.isascii() else text


def to_ass(cues: list[Cue], *, width: int = 1080, height: int = 1920, font: str = "Malgun Gothic",
           font_size: int = 72, margin_bottom: int = 320, box_opacity: float = 0.6,
           hook: Cue | None = None, hook_font_size: int = 96) -> str:
    """쇼츠 스타일 ASS: 하단 중앙(Alignment 2), 굵게, 흰 글씨 + 반투명 검은 박스(BorderStyle 3).

    hook 을 주면 첫 몇 초 동안 화면 위쪽에 큰 노란 글씨(검은 테두리)로 훅 문구를 띄운다.
    살짝 커졌다가 돌아오는 팝 효과 → 넘기려던 손가락을 멈추게 하는 용도.
    """
    alpha = f"{round((1 - min(max(box_opacity, 0.0), 1.0)) * 255):02X}"
    box = f"&H{alpha}000000"          # &HAABBGGRR, AA=00 불투명
    pad = max(8, font_size // 4)      # BorderStyle 3에서 Outline 값 = 박스 여백
    side = max(40, width // 18)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,{font},{font_size},&H00FFFFFF,&H000000FF,{box},{box},-1,0,0,0,100,100,0,0,3,{pad},0,2,{side},{side},{margin_bottom},1
Style: Hook,{font},{hook_font_size},&H0000F0FF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,{max(4, hook_font_size // 10)},{max(2, hook_font_size // 30)},8,{side},{side},{int(height * 0.18)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Shorts,,0,0,0,,{_ass_escape(c.text)}"
             for c in cues]
    if hook is not None and hook.text.strip():
        pop = r"{\fad(60,250)\fscx80\fscy80\t(0,160,\fscx112\fscy112)\t(160,300,\fscx100\fscy100)}"
        lines.insert(0, f"Dialogue: 1,{_ass_time(hook.start)},{_ass_time(hook.end)},Hook,,0,0,0,,"
                        f"{pop}{_ass_escape(hook_display(hook.text))}")
    return head + "\n".join(lines) + "\n"


def ass_style_from_config(video_cfg: dict) -> dict:
    return dict(width=int(video_cfg["width"]), height=int(video_cfg["height"]),
                font=str(video_cfg.get("subtitle_font") or "Malgun Gothic"),
                font_size=int(video_cfg.get("subtitle_font_size") or 72),
                margin_bottom=int(video_cfg.get("subtitle_margin_bottom") or 0),
                box_opacity=float(video_cfg.get("subtitle_box_opacity", 0.6)),
                hook_font_size=int(video_cfg.get("hook_font_size") or 96))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from ..generator import tts_lines

    parser = argparse.ArgumentParser(description="자막(SRT/ASS) 생성 단독 테스트")
    parser.add_argument("script", help="대본 텍스트 파일")
    parser.add_argument("--words", help="tts 단독 테스트가 만든 tts_words.json (없으면 글자 수로 추정)")
    parser.add_argument("--out", default="samples/subs")
    parser.add_argument("--max-chars", type=int, default=14)
    args = parser.parse_args(argv)

    text = " ".join(tts_lines(Path(args.script).read_text(encoding="utf-8")))
    if args.words:
        words = [Word(**w) for w in json.loads(Path(args.words).read_text(encoding="utf-8"))]
        end = words[-1].end + 0.3 if words else 1.0
    else:
        words, end = [], len(text) / 6.5
    cues = cues_for_scene(text, words, 0.0, end, args.max_chars)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "subtitles.srt").write_text(to_srt(cues), encoding="utf-8")
    (out / "subtitles.ass").write_text(to_ass(cues), encoding="utf-8")
    print(f"자막 {len(cues)}개 → {out / 'subtitles.srt'}, {out / 'subtitles.ass'}")
    for c in cues[:8]:
        print(f"  {c.start:6.2f}~{c.end:6.2f}s  {c.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
