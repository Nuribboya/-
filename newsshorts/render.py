from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from newsshorts.audience import GENERAL, Audience
from newsshorts.tts import Cue

WIDTH, HEIGHT = 1080, 1920
FPS = 30

# Nanum Gothic Bold (SIL Open Font License) from the google/fonts repo.
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/NanumGothic-Bold.ttf"
FONT_CACHE = Path.home() / ".cache" / "newsshorts" / "NanumGothic-Bold.ttf"
SYSTEM_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
]

BG_TOP = (14, 18, 32)
BG_BOTTOM = (34, 12, 18)
ACCENT = (230, 40, 50)
WHITE = (255, 255, 255)
SUB_COLOR = (255, 228, 92)
CARD_BG = (245, 245, 245)
CARD_TEXT = (20, 20, 20)
MUTED = (180, 186, 200)
SUB_BOX = (0, 0, 0)


def resolve_font(configured: str | None = None) -> str | None:
    """Find a font that can draw Korean; downloads Nanum Gothic once if needed."""
    if configured and Path(configured).exists():
        return configured
    if FONT_CACHE.exists():
        return str(FONT_CACHE)
    for cand in SYSTEM_FONT_CANDIDATES:
        if Path(cand).exists():
            return cand
    try:
        FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(FONT_URL, FONT_CACHE)
        return str(FONT_CACHE)
    except Exception as exc:
        print(f"[warn] no Korean font available ({exc}); text will not render correctly")
        return None


def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap by pixel width; words wider than a line are split by character."""
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if font.getlength(candidate) <= max_width:
            line = candidate
            continue
        if line:
            lines.append(line)
            line = ""
        for ch in word:
            if font.getlength(line + ch) > max_width and line:
                lines.append(line)
                line = ""
            line += ch
    if line:
        lines.append(line)
    return lines


def _background() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        color = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
        draw.line([(0, y), (WIDTH, y)], fill=color)
    return img


def _draw_lines(draw, lines, font, y, fill, spacing=1.25, stroke=0, stroke_fill=(0, 0, 0)):
    size = font.size
    for line in lines:
        w = font.getlength(line)
        draw.text(((WIDTH - w) / 2, y), line, font=font, fill=fill,
                  stroke_width=stroke, stroke_fill=stroke_fill)
        y += int(size * spacing)
    return y


def base_frame(title: str, quote: str, source: str, font_path: str | None,
               audience: Audience = GENERAL, badge: str = "뉴스") -> Image.Image:
    """Static part of every frame: badge, source, title and quote card."""
    img = _background()
    draw = ImageDraw.Draw(img)
    margin = 70
    scale = audience.font_scale

    badge_font = _font(font_path, 52)
    badge_w = int(badge_font.getlength(badge)) + 70
    draw.rounded_rectangle([margin, 150, margin + badge_w, 230], radius=14, fill=ACCENT)
    draw.text((margin + badge_w / 2, 190), badge, font=badge_font, fill=WHITE, anchor="mm")
    draw.text((margin + badge_w + 30, 190), source, font=_font(font_path, 44), fill=MUTED, anchor="lm")

    title_font = _font(font_path, int(78 * scale))
    title_lines = wrap(title, title_font, WIDTH - 2 * margin)[:4]
    y = _draw_lines(draw, title_lines, title_font, 300, WHITE)

    quote_font = _font(font_path, int(54 * scale))
    quote_lines = wrap(f"“{quote}”", quote_font, WIDTH - 2 * margin - 80)[:5]
    card_top = y + 60
    card_bottom = card_top + 60 + len(quote_lines) * int(quote_font.size * 1.35)
    draw.rounded_rectangle([margin, card_top, WIDTH - margin, card_bottom], radius=28, fill=CARD_BG)
    draw.rectangle([margin, card_top, margin + 14, card_bottom], fill=ACCENT)
    _draw_lines(draw, quote_lines, quote_font, card_top + 30, CARD_TEXT, spacing=1.35)
    return img


def cue_frame(base: Image.Image, cue: Cue, progress: float, font_path: str | None,
              audience: Audience = GENERAL) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)
    sub_font = _font(font_path, int(66 * audience.font_scale))
    lines = wrap(cue.text, sub_font, WIDTH - 140)
    if len(lines) > audience.subtitle_lines:
        # Shrink rather than cut off: a dropped subtitle line loses words the narrator says.
        sub_font = _font(font_path, int(sub_font.size * 0.85))
        lines = wrap(cue.text, sub_font, WIDTH - 140)
    line_h = int(sub_font.size * 1.25)
    y = 1450 - (len(lines) - 1) * line_h // 2
    if audience.subtitle_box:
        draw.rounded_rectangle([40, y - 30, WIDTH - 40, y + len(lines) * line_h + 10],
                               radius=24, fill=SUB_BOX)
    _draw_lines(draw, lines, sub_font, y, SUB_COLOR, stroke=0 if audience.subtitle_box else 5)
    draw.rectangle([0, HEIGHT - 16, int(WIDTH * progress), HEIGHT], fill=ACCENT)
    return img


def render_video(
    title: str,
    quote: str,
    source: str,
    cues: list[Cue],
    audio: Path,
    total: float,
    out_mp4: Path,
    font_path: str | None,
    audience: Audience = GENERAL,
    badge: str = "뉴스",
) -> Path:
    """Render one still per subtitle cue and stitch them to the narration with ffmpeg."""
    work = out_mp4.parent / "frames"
    work.mkdir(parents=True, exist_ok=True)
    base = base_frame(title, quote, source, font_path, audience, badge)

    concat_lines = []
    # Hold the base frame until the first cue starts.
    if cues[0].start > 0.05:
        p = work / "f000_lead.png"
        base.save(p)
        concat_lines += [f"file '{p.name}'", f"duration {cues[0].start:.3f}"]
    for i, cue in enumerate(cues):
        end = cues[i + 1].start if i + 1 < len(cues) else total
        p = work / f"f{i + 1:03d}.png"
        cue_frame(base, cue, min(1.0, end / total), font_path, audience).save(p)
        concat_lines += [f"file '{p.name}'", f"duration {max(0.04, end - cue.start):.3f}"]
    # The concat demuxer ignores the last entry's duration unless the file repeats.
    concat_lines.append(f"file '{p.name}'")
    (work / "list.txt").write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
         "-i", str(audio),
         "-vf", f"fps={FPS},format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "128k",
         "-t", f"{total:.2f}", "-movflags", "+faststart",
         str(out_mp4)],
        check=True,
    )
    return out_mp4
