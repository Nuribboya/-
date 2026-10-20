from __future__ import annotations

from pathlib import Path

from newsshorts.config import Config


def notify_draft(config: Config, draft: Path, title: str) -> None:
    """Send the draft video to Telegram so it can be reviewed from a phone."""
    if not (config.telegram_bot_token and config.telegram_chat_id):
        return
    import requests

    caption = f"새 쇼츠 초안\n{title}\n\n승인: python -m newsshorts.cli approve {draft}"
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendVideo"
    try:
        with open(draft / "video.mp4", "rb") as f:
            requests.post(url, data={"chat_id": config.telegram_chat_id, "caption": caption[:1000]},
                          files={"video": f}, timeout=60).raise_for_status()
    except Exception as exc:
        print(f"[warn] Telegram notify failed: {exc}")
