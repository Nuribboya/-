from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from newsshorts.audience import get_audience
from newsshorts.config import Config
from newsshorts.labels import finalize, report_type, title_labels
from newsshorts.render import render_video, resolve_font
from newsshorts.script import fact_check, make_script
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
    script = finalize(make_script(item, config.model, use_claude=use_claude, audience=audience), item)
    labels = title_labels(item)
    if script.generator == "claude":
        try:
            check = fact_check(item, script, config.model)
        except Exception as exc:
            check = {"ok": False, "problems": [f"검수 실패: {exc}"]}
    else:
        check = {"ok": False, "problems": ["템플릿 대본이라 검수하지 않음"]}
    audio = draft / "narration.mp3"
    rate = config.voice_rate or audience.voice_rate
    cues = synthesize(script.sentences, config.voice, rate, audio, use_tts=use_tts)
    total = max(audio_duration(audio), cues[-1].end + 0.3)

    font = resolve_font(config.font_path)
    badge = next((l for l in labels if l != "전망"), "뉴스")
    video = render_video(script.title, script.quote, f"{item.source} · {report_type(item)}", cues,
                         audio, total, draft / "video.mp4", font, audience, badge=badge)

    meta = {
        "status": "pending",
        "item": item.to_dict(),
        "script": script.to_dict(),
        "audience": audience.name,
        "labels": labels,
        "report_type": report_type(item),
        "fact_check": check,
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


def publish_draft(config: Config, draft: Path, auto: bool = False) -> str:
    from newsshorts.upload import upload_short

    meta = load_draft(draft)
    script = meta["script"]
    # Title/description can be edited in draft.json before approving.
    video_id = upload_short(config, draft / meta["video"], script["title"],
                            f"{script['description']}\n\n원문: {meta['item']['link']}",
                            script["hashtags"])
    meta.update(status="uploaded", youtube_id=video_id, auto=auto,
                uploaded_at=datetime.now().isoformat(timespec="seconds"))
    save_draft(draft, meta)
    return video_id


def reject_draft(draft: Path) -> None:
    meta = load_draft(draft)
    meta["status"] = "rejected"
    save_draft(draft, meta)


def takedown_draft(config: Config, draft: Path) -> str:
    from newsshorts.upload import delete_video

    meta = load_draft(draft)
    delete_video(config, meta["youtube_id"])
    meta["status"] = "taken_down"
    save_draft(draft, meta)
    return meta["youtube_id"]


def auto_published_today(config: Config) -> int:
    today = datetime.now().date().isoformat()
    return sum(
        1 for d in list_drafts(config, "uploaded")
        if (m := load_draft(d)).get("auto") and m.get("uploaded_at", "").startswith(today)
    )


def list_drafts(config: Config, status: str | None = None) -> list[Path]:
    root = Path(config.output_dir)
    if not root.exists():
        return []
    drafts = sorted(p for p in root.iterdir() if (p / "draft.json").exists())
    if status:
        drafts = [d for d in drafts if load_draft(d)["status"] == status]
    return drafts
