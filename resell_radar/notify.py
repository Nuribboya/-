from __future__ import annotations

import json
import urllib.parse
import urllib.request

from resell_radar.models import Release, Score, fmt_kst

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def _call(self, method: str, params: dict) -> dict:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(API.format(token=self.token, method=method), data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)

    def send(self, text: str) -> None:
        self._call("sendMessage", {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"})


def find_chat_id(token: str) -> str | None:
    """Pick up the chat id of whoever last messaged the bot (used during setup)."""
    result = Telegram(token, "")._call("getUpdates", {})
    for update in reversed(result.get("result", [])):
        chat = (update.get("message") or {}).get("chat")
        if chat:
            return str(chat["id"])
    return None


def format_message(release: Release, score: Score, kind: str) -> str:
    head = "🔔 응모 마감 임박" if kind == "deadline" else f"🆕 {score.verdict} ({score.level}급)"
    lines = [head, release.name]
    if release.retail_price:
        lines.append(f"정가 {release.retail_price:,}원")
    if score.est_profit is not None:
        tag = "추정" if score.price_is_estimate else "시세 기준"
        lines.append(f"예상 순수익 {score.est_profit:,}원 ({tag})")
    lines.append(f"응모 {fmt_kst(release.entry_start)} ~ {fmt_kst(release.entry_end)}")
    lines.append("근거: " + " / ".join(score.reasons))
    if release.url:
        lines.append(release.url)
    return "\n".join(lines)
