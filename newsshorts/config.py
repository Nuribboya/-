from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Source:
    name: str
    url: str
    # Optional per-source keyword filter; empty means "take everything".
    keywords: list[str] = field(default_factory=list)


@dataclass
class Config:
    sources: list[Source] = field(default_factory=list)
    # Global keyword filter applied on top of per-source keywords.
    keywords: list[str] = field(default_factory=list)
    # Who the videos are for: "senior" (50~70s) or "general". See audience.py.
    audience: str = "senior"
    # Skip items whose audience topic score is below this (0 = keep everything).
    min_topic_score: int = 0
    poll_seconds: int = 120
    output_dir: str = "drafts"
    state_file: str = "drafts/seen.json"
    model: str = "claude-opus-5-5"
    voice: str = "ko-KR-InJoonNeural"
    # None uses the audience preset's speaking rate.
    voice_rate: str | None = None
    font_path: str | None = None
    # Upload settings (only used by `approve`).
    youtube_client_secret: str = "client_secret.json"
    youtube_token: str = "youtube_token.json"
    youtube_privacy: str = "public"
    youtube_category_id: str = "25"  # News & Politics
    # Optional Telegram notification when a draft is ready.
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


def load_config(path: str | Path) -> Config:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    sources = [Source(**s) for s in raw.pop("sources", [])]
    return Config(sources=sources, **raw)
