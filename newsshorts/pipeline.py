from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from newsshorts.audience import get_audience
from newsshorts.config import Config
from newsshorts.render import render_video, resolve_font
from newsshorts.script import make_script
from newsshorts.sources import NewsItem
from newsshorts.tts import audio_duration, synthesize


def _slug(text: str, limit: int = 30) -> str:
    s = re.sub(r"[^\w가-힣]+", "_", text).strip("_")
    return s[:limit] or "item"


def make_draft(item: NewsItem, config: Config, use_claude: bool = True, use_tts: bool = True) -> Path:
    """Turn one news item into a draft folder: script.json, narration, video.mp4."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    draft = Path(config.output_dir) / f"{stamp}_{_slug(item.title)}"
    draft.mkdir(parents=True, exist_ok=True)

    audience = get_audience(config.audience)
    script = make_script(item, config.model, use_claude=use_claude, audience=audience)
    audio = draft / "narration.mp3"
    rate = config.voice_rate or audience.voice_rate
    cues = synthesize(script.sentences, config.voice, rate, audio, use_tts=use_tts)
    total = max(audio_duration(audio), cues[-1].end + 0.3)

    font = resolve_font(config.font_path)
    video = render_video(script.title, script.quote, item.source, cues, audio, total,
                         draft / "video.mp4", font, audience)

    meta = {
        "status": "pending",
        "item": item.to_dict(),
        "script": script.to_dict(),
        "audience": audience.name,
        "duration": round(total, 2),
        "video": video.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    (draft / "draft.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return draft


def load_draft(draft: Path) -> dict:
    return json.loads((draft / "draft.json").read_text(encoding="utf-8"))


def save_draft(draft: Path, meta: dict) -> None:
    (draft / "draft.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def list_drafts(config: Config, status: str | None = None) -> list[Path]:
    root = Path(config.output_dir)
    if not root.exists():
        return []
    drafts = sorted(p for p in root.iterdir() if (p / "draft.json").exists())
    if status:
        drafts = [d for d in drafts if load_draft(d)["status"] == status]
    return drafts
