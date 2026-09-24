"""텔레그램 봇 알림 (python-telegram-bot, 무료 Bot API).

알림 1건은 다음과 같이 나눠 보낸다.
1) 요약 메시지   : 채널명, 하락 지표, 최근 영상 요약, 상위 영상 패턴
2) Claude 프롬프트: <pre> 블록이라 텔레그램 앱에서 탭 한 번으로 복사 가능
3) (옵션) 마크다운 리포트 파일 첨부
"""

from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from .analyzer import ChannelAnalysis
from .report import claude_prompt, fnum, fpct, metric_label, pattern_lines, status_text

log = logging.getLogger(__name__)

MAX_LEN = 4096


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """텔레그램 길이 제한에 맞춰 줄 단위로 자른다."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # 한 줄이 너무 길면 강제로 자름
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def build_alert_html(a: ChannelAnalysis, tz: ZoneInfo) -> str:
    e = html.escape
    lines = [
        f"<b>{e(status_text(a))}: {e(a.channel_title)}</b>",
        f"{a.analyzed_at.astimezone(tz):%Y-%m-%d %H:%M %Z}",
        "",
        "<b>📉 하락 지표</b>",
    ]
    lines += [f"• {e(r)}" for r in a.reasons] or ["• (없음)"]
    if a.drop_pct is not None:
        lines.append(f"• {e(metric_label(a))}: 최근 {fnum(a.recent_avg)} / 이전 {fnum(a.baseline_avg)}"
                     f" ({fpct(a.change_pct)})")
    if a.weekly_views is not None:
        lines.append(f"• 최근 7일 채널 조회수 +{fnum(a.weekly_views)}"
                     + (f" (전주 대비 {fpct(a.weekly_change_pct)})" if a.weekly_change_pct is not None else ""))

    lines += ["", f"<b>🎬 최근 영상 {len(a.recent)}개</b>"]
    for p in a.recent:
        lines.append(f"• {e(p.video.title)} — 조회 {fnum(p.views)}, 일평균 {fnum(p.views_per_day)}")

    lines += ["", f"<b>🏆 상위 성과 Top {len(a.top)}</b>"]
    for i, p in enumerate(a.top, 1):
        lines.append(f"{i}. {e(p.video.title)} [{e(p.video.category_name or '미분류')}]"
                     f" — {fnum(p.metric)}")

    lines += ["", "<b>🔎 상위 영상 패턴</b>"] + [f"• {e(x)}" for x in pattern_lines(a)]
    return "\n".join(lines)


def build_generation_html(g, preview_lines: int = 6) -> str:
    """생성된 주제/제목/대본 요약 (전문은 첨부 파일)."""
    e = html.escape
    lines = [f"<b>🎬 다음 영상 기획 자동 생성</b> ({e(g.model)})", "", "<b>주제 후보</b>"]
    for i, t in enumerate(g.topics):
        mark = " ⭐" if i == g.best else ""
        lines.append(f"{i + 1}. {e(t['topic'])}{mark}")
        lines += [f"   · {e(title)}" for title in t["titles"]]
    if g.script_lines:
        lines += ["", f"<b>📝 대본</b> — {e(g.title or '')}"]
        lines += [e(s) for s in g.script_lines[:preview_lines]]
        if len(g.script_lines) > preview_lines:
            lines.append(f"… (전체 {len(g.script_lines)}문장, 첨부 파일 참고)")
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, bot=None):
        self.chat_id = chat_id
        if bot is None:
            from telegram import Bot

            bot = Bot(token)
        self.bot = bot

    async def _send_async(self, messages: list[tuple[str, str | None]], documents: list[Path]):
        from telegram import LinkPreviewOptions

        async with self.bot:
            for text, mode in messages:
                await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode=mode,
                                            link_preview_options=LinkPreviewOptions(is_disabled=True))
            for document in documents:
                with open(document, "rb") as f:
                    await self.bot.send_document(chat_id=self.chat_id, document=f,
                                                 filename=document.name)

    def _run(self, messages, documents: list[Path] | None = None) -> None:
        asyncio.run(self._send_async(messages, documents or []))

    def send_text(self, text: str) -> None:
        self._run([(chunk, None) for chunk in split_message(text)])

    def send_alert(self, a: ChannelAnalysis, tz: ZoneInfo, report_path: Path | None = None,
                   request: str | None = None, generation=None,
                   generation_error: str | None = None) -> None:
        """하락 감지 알림. generation(생성 결과)이 있으면 주제/대본 요약과 파일을 함께 보낸다."""
        messages: list[tuple[str, str | None]] = []
        for chunk in split_message(build_alert_html(a, tz)):
            messages.append((chunk, "HTML"))
        documents: list[Path] = []
        if generation is not None:
            for chunk in split_message(build_generation_html(generation)):
                messages.append((chunk, "HTML"))
            documents += [p for p in (generation.output_path, generation.script_path) if p]
        else:
            if generation_error:
                messages.append((f"⚠️ 주제/대본 자동 생성 실패: {html.escape(generation_error[:500])}", "HTML"))
            # 로컬 생성이 없을 때는 Claude 채팅에 붙여넣을 블록을 대신 보낸다
            header = "📋 아래 블록을 탭해서 복사 → Claude 채팅에 붙여넣기\n"
            for i, chunk in enumerate(split_message(claude_prompt(a, tz, request), MAX_LEN - 200)):
                prefix = header if i == 0 else ""
                messages.append((f"{prefix}<pre>{html.escape(chunk)}</pre>", "HTML"))
        if report_path is not None:
            documents.append(report_path)
        self._run(messages, documents)
        log.info("텔레그램 알림 전송: %s", a.channel_title)


async def _fetch_chat_ids(token: str) -> list[tuple[str, str]]:
    from telegram import Bot

    async with Bot(token) as bot:
        updates = await bot.get_updates(timeout=5)
    seen: dict[str, str] = {}
    for u in updates:
        chat = u.effective_chat
        if chat:
            seen[str(chat.id)] = chat.title or chat.full_name or chat.username or ""
    return list(seen.items())


def fetch_chat_ids(token: str) -> list[tuple[str, str]]:
    """봇에게 최근 메시지를 보낸 대화방의 (chat_id, 이름) 목록. 챗 ID 확인용."""
    return asyncio.run(_fetch_chat_ids(token))
