"""채널별 마크다운 리포트 생성/저장.

저장 위치
- reports/YYYY-MM-DD/HHMM_<채널>.md   : 수집 시점별 아카이브
- reports/latest/<채널>.md            : 항상 최신본으로 덮어씀

리포트 맨 아래 "Claude에 붙여넣기용" 블록은 그대로 복사해 Claude 채팅에 붙이면
다음 주제/대본을 바로 물어볼 수 있게 구성되어 있다.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .analyzer import ChannelAnalysis, VideoPerf

METRIC_LABELS = {
    "views_per_day": "일평균 조회수(누적 조회수/게시 후 경과일)",
    "views_at_age": "게시 후 {age}일 시점 조회수",
}

DEFAULT_CLAUDE_REQUEST = """\
1. 위 데이터로 볼 때 조회수 흐름이 바뀐 원인을 가설 3가지로 정리해줘.
2. 상위 성과 영상의 패턴을 살려서 다음 영상 주제 5개를 추천해줘. (주제마다 제목안 2개 + 썸네일 문구 1개)
3. 그중 가장 유망한 주제 1개로 대본 초안을 써줘. (도입 15초 훅 포함)"""


# ---- 포맷 도우미 ------------------------------------------------------------

def fnum(v: float | int | None, digits: int = 0) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}"


def fpct(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "-"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def fduration(sec: float | None) -> str:
    if not sec:
        return "-"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def metric_label(a: ChannelAnalysis) -> str:
    return METRIC_LABELS[a.metric_used].format(age=a.settings["age_days"])


def status_text(a: ChannelAnalysis) -> str:
    if a.slowdown:
        return "🚨 성장 둔화 감지"
    if a.insufficient:
        return "⏳ 판단 보류 (데이터 부족)"
    return "✅ 정상"


def safe_name(text: str) -> str:
    name = re.sub(r"[^\w가-힣.-]+", "_", text).strip("_.")
    return name[:60] or "channel"


def _local(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def _weekly_line(a: ChannelAnalysis) -> str:
    if a.weekly_views is None:
        return "데이터 수집 중 (7일 이상 필요)"
    s = f"{fnum(a.weekly_views)}회"
    if a.weekly_change_pct is not None:
        s += f" (전주 대비 {fpct(a.weekly_change_pct)})"
    elif a.prev_weekly_views is None:
        s += " (전주 비교는 14일 이상 수집 후)"
    return s


def _format_line(p: dict) -> str:
    return (f"숏폼 비율 {fpct(p['shorts_ratio'] * 100, False)}"
            + (f" (채널 전체 {fpct(p['channel_shorts_ratio'] * 100, False)})"
               if p.get("channel_shorts_ratio") is not None else "")
            + f", 평균 길이 {fduration(p['avg_duration_sec'])}"
            + f" (채널 전체 {fduration(p['channel_avg_duration_sec'])})")


def _timing_line(p: dict) -> str:
    wd = ", ".join(f"{d}({c})" for d, c in p["weekdays"][:3])
    hr = ", ".join(f"{h}시({c})" for h, c in p["hours"][:3])
    return f"요일 {wd} / 시간 {hr}"


def pattern_lines(a: ChannelAnalysis) -> list[str]:
    p = a.patterns
    if not p:
        return ["(분석 가능한 영상이 아직 없습니다)"]
    lines = [
        "반복 키워드: " + (", ".join(p["keywords"]) if p["keywords"] else "뚜렷한 공통 키워드 없음"),
        "카테고리: " + ", ".join(f"{c}({n})" for c, n in p["categories"]),
        "형식: " + _format_line(p),
        "게시 타이밍: " + _timing_line(p),
        f"제목 평균 길이: {p['avg_title_len']:.0f}자",
    ]
    if p.get("like_rate") is not None:
        lines.append(f"좋아요율: {fpct(p['like_rate'], False)}"
                     f" (채널 전체 {fpct(p.get('channel_like_rate'), False)})")
    return lines


def _video_brief(p: VideoPerf, a: ChannelAnalysis) -> str:
    kind = "숏폼" if p.is_short else "롱폼"
    parts = [f"게시 {p.age_days:.1f}일 경과", kind, f"조회수 {fnum(p.views)}",
             f"일평균 {fnum(p.views_per_day)}"]
    if a.metric_used == "views_at_age" and p.views_at_age is not None:
        parts.append(f"{a.settings['age_days']}일 시점 {fnum(p.views_at_age)}")
    if p.like_rate is not None:
        parts.append(f"좋아요율 {fpct(p.like_rate, False)}")
    return f"{p.video.title} ({', '.join(parts)})"


# ---- Claude 프롬프트 --------------------------------------------------------

def channel_context(a: ChannelAnalysis, tz: ZoneInfo) -> str:
    """채널 현황 요약 텍스트 (Claude 붙여넣기 블록과 Ollama 프롬프트가 함께 쓴다)."""
    lines = [
        f"채널: {a.channel_title}",
        f"분석 시각: {_local(a.analyzed_at, tz)}",
        f"상태: {status_text(a).split(' ', 1)[1]}",
    ]
    if a.subscribers is not None:
        lines.append(f"구독자: {fnum(a.subscribers)}명")
    if a.drop_pct is not None:
        lines.append(
            f"최근 {len(a.recent)}개 영상 평균 조회수 증가율: {fpct(a.change_pct)} "
            f"(이전 {len(a.baseline)}개 대비, 지표: {metric_label(a)} "
            f"{fnum(a.recent_avg)} vs {fnum(a.baseline_avg)})"
        )
    elif a.insufficient:
        lines.append(f"영상 비교: {a.insufficient}")
    lines.append(f"최근 7일 채널 조회수 증가량: {_weekly_line(a)}")
    for r in a.reasons:
        lines.append(f"둔화 근거: {r}")

    lines += ["", f"[최근 영상 성과 (최신순 {len(a.recent)}개)]"]
    lines += [f"- {_video_brief(p, a)}" for p in a.recent] or ["- (없음)"]

    lines += ["", f"[상위 성과 영상 Top {len(a.top)}]"]
    for i, p in enumerate(a.top, 1):
        lines.append(f"{i}. {p.video.title} / {p.video.category_name or '미분류'} / "
                     f"{fduration(p.video.duration_seconds)} / {metric_label(a)} {fnum(p.metric)}")
    if not a.top:
        lines.append("- (없음)")

    lines += ["", "[상위 영상 패턴]"] + [f"- {x}" for x in pattern_lines(a)]
    return "\n".join(lines)


def claude_prompt(a: ChannelAnalysis, tz: ZoneInfo, request: str | None = None) -> str:
    return "\n".join([channel_context(a, tz), "", "요청:", (request or DEFAULT_CLAUDE_REQUEST).strip()])


# ---- 마크다운 리포트 --------------------------------------------------------

def render_markdown(a: ChannelAnalysis, tz: ZoneInfo, request: str | None = None,
                    table_rows: int = 15) -> str:
    s = a.settings
    agg = "중앙값" if s["aggregate"] == "median" else "평균"
    out = [
        f"# 📊 {a.channel_title} 성과 리포트",
        "",
        f"- 분석 시각: {_local(a.analyzed_at, tz)}",
        f"- 채널 ID: `{a.channel_id}`",
        f"- 상태: **{status_text(a)}**",
        "",
        "## 핵심 지표",
        "",
        "| 지표 | 값 |",
        "| --- | --- |",
        f"| 구독자 | {fnum(a.subscribers)} |",
        f"| 비교 지표 | {metric_label(a)} |",
        f"| 최근 {len(a.recent)}개 영상 {agg} | {fnum(a.recent_avg, 1)} |",
        f"| 이전 {len(a.baseline)}개 영상 {agg} | {fnum(a.baseline_avg, 1)} |",
        f"| 변화율 | {fpct(a.change_pct)} (둔화 기준: -{s['drop_threshold_pct']}%) |",
        f"| 최근 7일 채널 조회수 증가량 | {_weekly_line(a)} |",
        "",
    ]
    if a.metric_note:
        out += [f"> ℹ️ {a.metric_note}", ""]
    if a.insufficient:
        out += [f"> ⏳ {a.insufficient}", ""]
    if a.reasons:
        out += ["## 🚨 둔화 판단 근거", ""] + [f"- {r}" for r in a.reasons] + [""]

    out += [
        "## 최근 영상 성과",
        "",
        "| 게시일 | 제목 | 경과일 | 조회수 | 일평균 | 최근 7일 증가 | 좋아요율 | 댓글 | 형식 | 비교 그룹 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    recent_ids = {p.video.video_id for p in a.recent}
    base_ids = {p.video.video_id for p in a.baseline}
    for p in a.videos[:table_rows]:
        vid = p.video.video_id
        group = "최근" if vid in recent_ids else "이전" if vid in base_ids else "-"
        title = p.video.title.replace("|", "\\|")
        out.append(
            f"| {p.video.published_at.astimezone(tz):%m-%d} "
            f"| [{title}](https://youtu.be/{vid}) | {p.age_days:.1f} | {fnum(p.views)} "
            f"| {fnum(p.views_per_day)} | {fnum(p.gain_7d)} | {fpct(p.like_rate, False)} "
            f"| {fnum(p.comments)} | {'숏폼' if p.is_short else '롱폼'} | {group} |"
        )
    out.append("")

    out += [f"## 🏆 상위 성과 영상 Top {len(a.top)}", ""]
    for i, p in enumerate(a.top, 1):
        out.append(
            f"{i}. [{p.video.title}](https://youtu.be/{p.video.video_id}) — "
            f"{p.video.category_name or '미분류'} · {fduration(p.video.duration_seconds)} · "
            f"조회수 {fnum(p.views)} · {metric_label(a)} {fnum(p.metric)}"
        )
    if not a.top:
        out.append("(없음)")
    out += ["", "## 상위 영상 패턴", ""] + [f"- {x}" for x in pattern_lines(a)] + [""]

    out += [
        "## 💬 Claude에 붙여넣기용",
        "",
        "아래 블록을 통째로 복사해 Claude 채팅에 붙여넣으세요.",
        "",
        "```text",
        claude_prompt(a, tz, request),
        "```",
        "",
    ]
    return "\n".join(out)


def save_report(a: ChannelAnalysis, reports_dir: Path, tz: ZoneInfo,
                request: str | None = None) -> Path:
    text = render_markdown(a, tz, request)
    local = a.analyzed_at.astimezone(tz)
    name = safe_name(a.channel_title)
    day_dir = reports_dir / local.strftime("%Y-%m-%d")
    latest_dir = reports_dir / "latest"
    day_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{local:%H%M}_{name}.md"
    path.write_text(text, encoding="utf-8")
    (latest_dir / f"{name}.md").write_text(text, encoding="utf-8")
    return path
