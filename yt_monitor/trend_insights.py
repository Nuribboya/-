"""유행 쇼츠 심층 분석 → 영상 만들기에 자동 반영.

① 숫자 · 제목 분석 (추가 설치 없음)
   - 잘 되는 길이: 시간당 조회수 상위 절반 영상의 길이 중앙값 → 대본 목표 길이(초)
   - 제목 패턴: 숫자 · 질문 · 대문자 강조 · 이모지 · POV · 1인칭 등 비율과 "있을 때 시간당 조회수가 몇 배인지"
   - 반응률: 좋아요/조회수, 댓글/조회수
   - 올린 시간: 시청자 시간대(미국 동부 등) 3시간 단위로 시간당 조회수가 높은 때 → 한국 시간으로 추천
② 썸네일(첫 화면) 분석 (Ollama 비전 모델, 예: qwen2.5vl:7b)
   - 상위 영상 썸네일에서 큰 글씨 · 얼굴 · 클로즈업 · 색감 · 훅 방식을 뽑아 첫 장면 스타일로 쓴다
③ 캐시: 수집 결과와 썸네일 분석을 data/trend_cache.json 에 저장 → 몇 시간 안에 다시 돌리면 쿼터 0 · 바로 시작

주의: 표본이 "이미 뜬 영상"뿐이라 참고용 경향입니다 (안 뜬 영상과 비교한 것이 아님).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import statistics
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

# 지역 → 시청자 시간대 (미국은 인구가 많은 동부 기준)
AUDIENCE_TZ = {"US": "America/New_York", "CA": "America/Toronto", "GB": "Europe/London", "AU": "Australia/Sydney",
               "KR": "Asia/Seoul", "JP": "Asia/Tokyo", "IN": "Asia/Kolkata", "DE": "Europe/Berlin",
               "FR": "Europe/Paris", "BR": "America/Sao_Paulo", "MX": "America/Mexico_City"}
TZ_NAMES = {"America/New_York": "미국 동부", "America/Toronto": "캐나다 동부", "Europe/London": "영국",
            "Australia/Sydney": "호주 동부", "Asia/Seoul": "한국", "Asia/Tokyo": "일본", "Asia/Kolkata": "인도",
            "Europe/Berlin": "독일", "Europe/Paris": "프랑스", "America/Sao_Paulo": "브라질",
            "America/Mexico_City": "멕시코"}

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")
# (키, 한국어 이름, 영어 설명, 판별)
TITLE_PATTERNS: list[tuple[str, str, str, Callable[[str], bool]]] = [
    ("number", "숫자 포함", "a number (e.g. '5 ...', '$3')", lambda t: bool(re.search(r"\d", t))),
    ("question", "질문형(?)", "a question", lambda t: "?" in t),
    ("caps", "대문자 강조 단어", "an ALL-CAPS word for emphasis",
     lambda t: bool(re.search(r"\b[A-Z]{3,}\b", t)) and not t.isupper()),
    ("emoji", "이모지", "an emoji", lambda t: bool(_EMOJI.search(t))),
    ("pov", "POV", "'POV'", lambda t: bool(re.search(r"\bpov\b", t, re.I))),
    ("first_person", "1인칭 (I tried / I made …)", "first person ('I tried', 'I made')",
     lambda t: bool(re.search(r"\b(i|i'm|my|we)\b", t, re.I))),
    ("how_why", "How / Why / What 으로 시작", "starting with How/Why/What",
     lambda t: bool(re.match(r"\s*(how|why|what)\b", t, re.I))),
    ("hashtag", "제목에 #해시태그", "a #hashtag in the title", lambda t: "#" in t),
]


@dataclass
class Insights:
    n: int = 0
    region: str = "US"
    duration_median: float | None = None
    duration_p25: float | None = None
    duration_p75: float | None = None
    top_duration_median: float | None = None       # 시간당 조회수 상위 절반의 길이 중앙값
    recommended_seconds: int | None = None
    buckets: list[dict] = field(default_factory=list)          # [{label, count, median_vph}]
    title_chars: float | None = None
    title_words: float | None = None
    patterns: list[dict] = field(default_factory=list)         # [{key, ko, en, share, lift}]
    like_rate: float | None = None
    comment_rate: float | None = None
    top_reactions: list[str] = field(default_factory=list)     # 댓글률 높은 영상 제목
    audience_tz: str = "America/New_York"
    best_hours: list[dict] = field(default_factory=list)       # [{start, count, median_vph}] 시청자 시간대
    upload_times: list[str] = field(default_factory=list)      # "01:00~04:00 (한국 시간) = 미국 동부 12~15시"
    thumbs: list[dict] = field(default_factory=list)           # 썸네일 분석 결과
    visual_summary_en: str = ""
    visual_summary_ko: str = ""
    vision_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _quantile(xs: list[float], q: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


BUCKETS = [(0, 15, "15초 이하"), (16, 30, "16~30초"), (31, 45, "31~45초"), (46, 60, "46~60초"), (61, 10**6, "1분 이상")]


def analyze(videos, now: datetime, *, region: str = "US", local_tz: ZoneInfo | None = None,
            min_seconds: int = 20, max_seconds: int = 58, top_n: int = 50) -> Insights:
    """① 숫자 · 제목 · 시간 분석."""
    vids = list(videos)[:top_n]
    ins = Insights(n=len(vids), region=region)
    if not vids:
        return ins
    vph = {v.video_id: v.views_per_hour(now) for v in vids}

    # 길이
    durs = [v.duration for v in vids if v.duration]
    ins.duration_median = _median(durs)
    ins.duration_p25, ins.duration_p75 = _quantile(durs, 0.25), _quantile(durs, 0.75)
    ranked = sorted((v for v in vids if v.duration), key=lambda v: vph[v.video_id], reverse=True)
    top = ranked[: max(5, len(ranked) // 2)]
    ins.top_duration_median = _median([v.duration for v in top])
    if ins.top_duration_median:
        ins.recommended_seconds = int(min(max_seconds, max(min_seconds, round(ins.top_duration_median / 5) * 5)))
    for lo, hi, label in BUCKETS:
        group = [vph[v.video_id] for v in vids if v.duration and lo <= v.duration <= hi]
        if group:
            ins.buckets.append({"label": label, "count": len(group), "median_vph": statistics.median(group)})

    # 제목
    titles = [v.title for v in vids]
    ins.title_chars = statistics.mean(len(t) for t in titles)
    ins.title_words = statistics.mean(len(t.split()) for t in titles)
    for key, ko, en, test in TITLE_PATTERNS:
        yes = [vph[v.video_id] for v in vids if test(v.title)]
        no = [vph[v.video_id] for v in vids if not test(v.title)]
        lift = (statistics.median(yes) / statistics.median(no)) if len(yes) >= 2 and len(no) >= 2 and \
            statistics.median(no) > 0 else None
        ins.patterns.append({"key": key, "ko": ko, "en": en, "share": len(yes) / len(vids), "lift": lift})

    # 반응
    like = [v.likes / v.views for v in vids if v.likes is not None and v.views]
    com = [v.comments / v.views for v in vids if v.comments is not None and v.views]
    ins.like_rate, ins.comment_rate = _median(like), _median(com)
    by_com = sorted((v for v in vids if v.comments and v.views), key=lambda v: v.comments / v.views, reverse=True)
    ins.top_reactions = [v.title for v in by_com[:3]]

    # 올린 시간 (시청자 시간대, 3시간 단위)
    ins.audience_tz = AUDIENCE_TZ.get(region.upper(), "UTC")
    atz = ZoneInfo(ins.audience_tz)
    blocks: dict[int, list[float]] = {}
    for v in vids:
        h = v.published_at.astimezone(atz).hour // 3 * 3
        blocks.setdefault(h, []).append(vph[v.video_id])
    rows = [{"start": h, "count": len(x), "median_vph": statistics.median(x)} for h, x in blocks.items()]
    enough = [r for r in rows if r["count"] >= 2] or rows
    ins.best_hours = sorted(enough, key=lambda r: (r["median_vph"], r["count"]), reverse=True)[:2]
    ltz = local_tz or ZoneInfo("Asia/Seoul")
    base = now.astimezone(atz).replace(minute=0, second=0, microsecond=0)
    for r in ins.best_hours:
        start = base.replace(hour=r["start"]).astimezone(ltz)
        end = start + timedelta(hours=3)
        where = TZ_NAMES.get(ins.audience_tz, ins.audience_tz)
        local_name = TZ_NAMES.get(str(ltz), str(ltz))
        same = str(ltz) == ins.audience_tz
        ins.upload_times.append(f"{start:%H}:00~{end:%H}:00 ({local_name} 시간)" +
                                ("" if same else f" = {where} {r['start']:02d}~{(r['start'] + 3) % 24:02d}시"))
    return ins


# ---- ② 썸네일 분석 ----------------------------------------------------------------------

VISION_PROMPT = """This is the thumbnail (first frame) of a viral YouTube Short.
Describe what makes it stop the scroll. Answer ONLY with JSON:
{"text_on_screen": "big text shown on the image, exactly, or empty", "subject": "main subject in a few words",
 "face": "none | one face close-up | several people | person far away",
 "emotion": "shocked / happy / serious / none", "shot": "extreme close-up | close-up | medium | wide",
 "colors": "2-3 dominant colors", "hook": "the visual hook in one short phrase"}"""


def _vision_client(cfg, model: str):
    from .ollama_client import OllamaClient

    o = cfg.ollama
    return OllamaClient(o["host"], model)


def analyze_thumbnails(videos, client, *, limit: int = 8, cache: dict | None = None,
                       session: requests.Session | None = None, cancel: threading.Event | None = None,
                       on_status: Callable[[str], None] | None = None) -> list[dict]:
    """② 상위 영상 썸네일을 비전 모델로 분석 (결과는 video_id로 캐시)."""
    from .generator import _extract_json

    cache = cache if cache is not None else {}
    session = session or requests.Session()
    out: list[dict] = []
    targets = [v for v in videos if getattr(v, "thumbnail", "")][:limit]
    for i, v in enumerate(targets, 1):
        if cancel is not None and cancel.is_set():
            break
        if v.video_id in cache:
            out.append(cache[v.video_id])
            continue
        if on_status:
            on_status(f"  썸네일 분석 {i}/{len(targets)}: {v.title[:40]}")
        try:
            img = session.get(v.thumbnail, timeout=15)
            img.raise_for_status()
            text = client.chat(VISION_PROMPT, json_mode=True, images=[base64.b64encode(img.content).decode()],
                               options={"temperature": 0.2}, cancel=cancel)
            data = _extract_json(text)
            if not isinstance(data, dict):
                raise ValueError("JSON 아님")
        except Exception as exc:  # 한 장 실패해도 계속
            from .ollama_client import GenerationCancelled, ModelMissing, OllamaUnavailable

            if isinstance(exc, (GenerationCancelled, ModelMissing, OllamaUnavailable)):
                raise
            log.warning("썸네일 분석 실패 (%s): %s", v.video_id, exc)
            continue
        row = {"video_id": v.video_id, "title": v.title,
               **{k: str(data.get(k, "")).strip() for k in ("text_on_screen", "subject", "face", "emotion",
                                                             "shot", "colors", "hook")}}
        cache[v.video_id] = row
        out.append(row)
    return out


def summarize_thumbs(thumbs: list[dict]) -> tuple[str, str]:
    """썸네일 분석 → (프롬프트용 영어 요약, 화면용 한국어 요약)."""
    if not thumbs:
        return "", ""
    n = len(thumbs)
    with_text = [t for t in thumbs if t.get("text_on_screen") and t["text_on_screen"].lower() not in ("none", "empty")]
    faces = [t for t in thumbs if t.get("face") and not t["face"].lower().startswith("none")]
    close = [t for t in thumbs if "close" in (t.get("shot") or "").lower()]
    emotions = [t["emotion"] for t in thumbs if t.get("emotion") and t["emotion"].lower() != "none"]
    hooks = [t["hook"] for t in thumbs if t.get("hook")][:6]
    texts = [t["text_on_screen"] for t in with_text][:5]
    pct = lambda xs: round(100 * len(xs) / n)  # noqa: E731
    en = [f"{n} top Shorts thumbnails: big text on screen {pct(with_text)}%, a face {pct(faces)}%, "
          f"close-up shots {pct(close)}%."]
    if emotions:
        en.append("Common emotions: " + ", ".join(sorted(set(emotions))[:4]) + ".")
    if hooks:
        en.append("Visual hooks: " + "; ".join(hooks) + ".")
    if texts:
        en.append("Examples of on-screen text: " + " | ".join(texts) + ".")
    ko = [f"썸네일 {n}개: 큰 글씨 {pct(with_text)}% · 얼굴 {pct(faces)}% · 클로즈업 {pct(close)}%"]
    if emotions:
        ko.append("표정: " + ", ".join(sorted(set(emotions))[:4]))
    if hooks:
        ko.append("눈길 끄는 요소: " + " / ".join(hooks))
    if texts:
        ko.append("화면 글씨 예: " + " | ".join(texts))
    return " ".join(en), "\n".join(ko)


def run_vision(cfg, ins: Insights, videos, *, cache: dict, cancel=None, on_status=None) -> None:
    """설정(trends.vision_model)의 비전 모델로 썸네일 분석. 모델이 없으면 설치 안내만 남기고 건너뛴다."""
    from .ollama_client import pull_command

    s = cfg.raw.get("trends") or {}
    if not s.get("vision", True):
        return
    model = s.get("vision_model") or "qwen2.5vl:7b"
    client = _vision_client(cfg, model)
    st = client.status()
    if not st.server_up:
        return
    if not st.model_present:
        ins.vision_note = f"썸네일 분석을 건너뜀: 비전 모델이 없습니다 → 명령 프롬프트에서 {pull_command(model)}"
        if on_status:
            on_status(ins.vision_note)
        return
    if on_status:
        on_status(f"썸네일(첫 화면) 분석 중… ({model})")
    try:
        ins.thumbs = analyze_thumbnails(videos, client, limit=int(s.get("vision_limit", 8)), cache=cache,
                                        cancel=cancel, on_status=on_status)
    finally:
        client.unload()          # 그래픽카드 메모리를 대본 모델에 돌려준다
    ins.visual_summary_en, ins.visual_summary_ko = summarize_thumbs(ins.thumbs)


# ---- 프롬프트 · 화면용 글 -------------------------------------------------------------

def _fmt_lift(lift: float | None) -> str:
    return f"x{lift:.1f}" if lift else "-"


def prompt_text(ins: Insights, lang: str = "en") -> str:
    """주제/대본 프롬프트에 붙이는 분석 요약."""
    if not ins.n:
        return ""
    strong = [p for p in ins.patterns if p["share"] >= 0.25 or (p["lift"] or 0) >= 1.3]
    strong.sort(key=lambda p: ((p["lift"] or 0), p["share"]), reverse=True)
    if lang == "en":
        lines = ["[What the numbers say about the trending Shorts]"]
        if ins.recommended_seconds:
            lines.append(f"- Best-performing length: about {ins.recommended_seconds} seconds "
                         f"(median {ins.duration_median:.0f}s, top half {ins.top_duration_median:.0f}s).")
        lines.append(f"- Titles average {ins.title_chars:.0f} characters / {ins.title_words:.0f} words.")
        for p in strong[:4]:
            lines.append(f"- {p['share']:.0%} of titles use {p['en']}"
                         + (f"; those get {_fmt_lift(p['lift'])} views/hour" if p["lift"] else "") + ".")
        if ins.visual_summary_en:
            lines.append("- " + ins.visual_summary_en)
        return "\n".join(lines)
    lines = ["[숫자로 본 유행 쇼츠]"]
    if ins.recommended_seconds:
        lines.append(f"- 잘 되는 길이: 약 {ins.recommended_seconds}초 (중앙값 {ins.duration_median:.0f}초)")
    lines.append(f"- 제목 평균 {ins.title_chars:.0f}자")
    for p in strong[:4]:
        lines.append(f"- 제목의 {p['share']:.0%}가 {p['ko']}" +
                     (f" (시간당 조회수 {_fmt_lift(p['lift'])})" if p["lift"] else ""))
    if ins.visual_summary_ko:
        lines.append("- " + ins.visual_summary_ko.replace("\n", " / "))
    return "\n".join(lines)


def title_hint(ins: Insights, lang: str = "en") -> str:
    """업로드 정보(제목 후보) 프롬프트용."""
    if not ins.n:
        return ""
    good = [p for p in ins.patterns if (p["lift"] or 0) >= 1.2 or p["share"] >= 0.4]
    good.sort(key=lambda p: ((p["lift"] or 0), p["share"]), reverse=True)
    if lang == "en":
        parts = [f"Trending Shorts titles average {ins.title_chars:.0f} characters."]
        if good:
            parts.append("Patterns that perform: " + "; ".join(
                f"{p['en']} ({p['share']:.0%} of titles, {_fmt_lift(p['lift'])} views/hour)" for p in good[:3]) + ".")
        return "[Title patterns]\n" + " ".join(parts)
    parts = [f"유행 쇼츠 제목 평균 {ins.title_chars:.0f}자."]
    if good:
        parts.append("잘 되는 패턴: " + "; ".join(f"{p['ko']}({p['share']:.0%}, {_fmt_lift(p['lift'])})"
                                                 for p in good[:3]))
    return "[제목 패턴]\n" + " ".join(parts)


def report_ko(ins: Insights) -> str:
    """🔥 트렌드 탭에 보여줄 한국어 분석 리포트."""
    if not ins.n:
        return ""
    L = [f"📊 유행 쇼츠 {ins.n}개 분석 (이미 뜬 영상 기준의 경향 · 참고용)", ""]
    if ins.duration_median:
        L.append(f"⏱ 길이: 중앙값 {ins.duration_median:.0f}초 (보통 {ins.duration_p25:.0f}~{ins.duration_p75:.0f}초), "
                 f"시간당 조회수 상위 절반은 {ins.top_duration_median:.0f}초")
        if ins.buckets:
            L.append("   " + " · ".join(f"{b['label']} {b['count']}개(시간당 {b['median_vph'] / 1000:.1f}K)"
                                      for b in ins.buckets))
        if ins.recommended_seconds:
            L.append(f"   → 대본 목표 길이를 {ins.recommended_seconds}초로 맞춥니다")
    L.append(f"✍ 제목: 평균 {ins.title_chars:.0f}자 · {ins.title_words:.0f}단어")
    for p in sorted(ins.patterns, key=lambda p: p["share"], reverse=True):
        if p["share"] >= 0.1:
            L.append(f"   {p['ko']}: {p['share']:.0%}" + (f" · 시간당 조회수 {_fmt_lift(p['lift'])}" if p["lift"] else ""))
    if ins.like_rate is not None:
        L.append(f"👍 반응: 좋아요율 {ins.like_rate:.1%}" +
                 (f" · 댓글률 {ins.comment_rate:.2%}" if ins.comment_rate is not None else ""))
        if ins.top_reactions:
            L.append("   댓글이 특히 많은 영상: " + " / ".join(t[:40] for t in ins.top_reactions))
    if ins.upload_times:
        L.append("🕒 잘 뜬 영상이 올라온 시간: " + " · ".join(ins.upload_times))
    if ins.visual_summary_ko:
        L += ["🖼 " + ins.visual_summary_ko.replace("\n", "\n   ")]
    elif ins.vision_note:
        L.append("🖼 " + ins.vision_note)
    return "\n".join(L)


# ---- ③ 캐시 -------------------------------------------------------------------------

def _video_to_dict(v) -> dict:
    d = {k: getattr(v, k) for k in v.__dataclass_fields__}
    d["published_at"] = v.published_at.isoformat()
    d["sources"] = sorted(v.sources)
    return d


def _video_from_dict(d: dict):
    from .trends import TrendVideo

    d = dict(d)
    d["published_at"] = datetime.fromisoformat(d["published_at"])
    d["sources"] = set(d.get("sources") or [])
    return TrendVideo(**{k: d[k] for k in TrendVideo.__dataclass_fields__ if k in d})


def cache_key(settings: dict) -> str:
    keys = ("region", "language", "title_language", "lookback_days", "shorts_max_seconds", "search_queries",
            "popular_pages", "min_views")
    return json.dumps({k: settings.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)


def load_cache(path: Path, settings: dict, now: datetime, max_hours: float) -> tuple[list, dict, datetime] | None:
    """(videos, thumbs, collected_at) — 설정이 같고 max_hours 안이면."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("key") != cache_key(settings):
        return None
    at = datetime.fromisoformat(data["collected_at"])
    if max_hours <= 0 or now - at > timedelta(hours=max_hours) or at > now + timedelta(minutes=5):
        return None
    try:
        return [_video_from_dict(d) for d in data["videos"]], data.get("thumbs") or {}, at
    except (KeyError, TypeError, ValueError):
        return None


def save_cache(path: Path, settings: dict, videos, thumbs: dict, collected_at: datetime) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"key": cache_key(settings), "collected_at": collected_at.astimezone(timezone.utc).isoformat(),
            "videos": [_video_to_dict(v) for v in videos], "thumbs": thumbs}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
