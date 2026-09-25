"""최근 유행하는 쇼츠 분석 → Ollama로 "지금 알고리즘 타기 좋은 주제" 추천 → 쇼츠 대본.

유튜브는 알고리즘 점수를 공개하지 않으므로 "최근 올라왔는데 조회수가 빠르게 오르는 영상"으로 추정한다.
- 수집: YouTube Data API
    videos.list(chart=mostPopular, regionCode=US|KR)       인기 급상승 (페이지당 1 unit)
    search.list(order=viewCount, publishedAfter=N일 전,     최근 N일 조회수 상위 짧은 영상
                videoDuration=short, regionCode=US|KR)       (검색 1회 100 unit)
    videos.list / channels.list                              조회수·길이 / 구독자 수 (50개당 1 unit)
  → 기본 설정으로 1회 약 210 unit (무료 쿼터 하루 10,000)
- 필터: 쇼츠 길이(shorts_max_seconds 이하), 최근 lookback_days 이내, 제목 언어(title_language)
- 언어: config 의 language (en = 미국 중심 영어, ko = 한국)에 따라 지역·프롬프트가 바뀐다
- 점수: 시간당 조회수 = 조회수 ÷ 게시 후 경과 시간
        떡상 = 조회수가 구독자 수의 breakout_ratio 배 이상 (작은 채널이 알고리즘을 탄 경우)
- 결과: 유행 목록 + 반복 키워드 → prompts/trend_topics.txt → 주제 후보
        → prompts/shorts_script.txt → 쇼츠 대본 → outputs/날짜/트렌드.md, 트렌드_대본.txt

단독 실행:
    python -m yt_monitor.trends            # 유행 쇼츠 수집 + 목록 출력 (Ollama 생성 없이)
    python -m yt_monitor.trends --generate # + 주제/대본 생성
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .collector import parse_duration
from .config import DEFAULT_TRENDS
from .db import from_iso, utcnow
from .report import fnum

log = logging.getLogger(__name__)

TREND_CHANNEL_ID = "trend"
TREND_TITLE = "트렌드"
TREND_TOPICS_PROMPT = "trend_topics.txt"
SHORTS_SCRIPT_PROMPT = "shorts_script.txt"

_STOP = {"shorts", "short", "쇼츠", "숏츠", "youtube", "유튜브", "the", "and", "for", "you", "with",
         "진짜", "정말", "이거", "그냥", "오늘", "영상", "ㅋㅋ", "ㅋㅋㅋ", "ㅎㅎ", "shortsvideo", "viral",
         "fyp", "trending", "funny", "구독", "좋아요", "shortvideo", "reels", "this", "that", "what", "how",
         "why", "when", "your", "are", "was", "his", "her", "they", "from", "have", "just", "can", "will",
         "not", "but", "all", "out", "get", "one", "its", "into", "about", "who", "did", "does", "don",
         "tiktok", "subscribe", "like"}
_JOSA = re.compile(r"(으로|에서|에게|까지|부터|처럼|보다|이랑|하고|은|는|이|가|을|를|의|에|도|만|와|과|로)$")


def title_matches(title: str, lang: str) -> bool:
    """제목 언어 필터. en: 한글/한자/가나가 없고 글자의 대부분이 라틴 문자, ko: 한글 포함."""
    if lang == "ko":
        return bool(re.search(r"[가-힣]", title))
    if lang == "en":
        if re.search(r"[가-힣\u3040-\u30ff\u4e00-\u9fff\u0400-\u04ff\u0600-\u06ff\u0900-\u097f]", title):
            return False
        letters = [c for c in title if c.isalpha()]
        latin = [c for c in letters if c.isascii()]
        return len(latin) >= 3 and len(latin) >= 0.8 * len(letters)
    return True


class TrendError(RuntimeError):
    pass


@dataclass
class TrendVideo:
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    published_at: datetime
    duration: int | None
    views: int
    likes: int | None = None
    comments: int | None = None
    subscribers: int | None = None
    category: str = ""
    tags: list[str] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)   # popular | search:<검색어>

    def hours(self, now: datetime) -> float:
        return max((now - self.published_at).total_seconds() / 3600, 1.0)

    def views_per_hour(self, now: datetime) -> float:
        return self.views / self.hours(now)

    @property
    def breakout(self) -> float | None:
        """조회수 ÷ 구독자 수 (구독자 비공개/0이면 None)."""
        return self.views / self.subscribers if self.subscribers else None

    @property
    def url(self) -> str:
        return f"https://youtube.com/shorts/{self.video_id}"


def _int(v) -> int | None:
    return int(v) if v not in (None, "") else None


class TrendCollector:
    def __init__(self, service, settings: dict | None = None):
        self.yt = service
        self.s = {**DEFAULT_TRENDS, **(settings or {})}
        self.units = 0          # 사용한 쿼터 (대략)

    # ---- 수집 ------------------------------------------------------------------
    def _popular(self) -> dict[str, dict]:
        items: dict[str, dict] = {}
        token = None
        for _ in range(int(self.s["popular_pages"])):
            resp = self.yt.videos().list(part="snippet,statistics,contentDetails", chart="mostPopular",
                                         regionCode=self.s["region"], maxResults=50, pageToken=token).execute()
            self.units += 1
            for it in resp.get("items", []):
                items[it["id"]] = it
            token = resp.get("nextPageToken")
            if not token:
                break
        return items

    def _search_ids(self, query: str, now: datetime) -> list[str]:
        after = (now - timedelta(days=float(self.s["lookback_days"]))).strftime("%Y-%m-%dT%H:%M:%SZ")
        kw = dict(part="id", type="video", order="viewCount", videoDuration="short", maxResults=50,
                  publishedAfter=after, regionCode=self.s["region"],
                  relevanceLanguage=self.s["language"])
        if query:
            kw["q"] = query
        resp = self.yt.search().list(**kw).execute()
        self.units += 100
        return [it["id"]["videoId"] for it in resp.get("items", []) if it.get("id", {}).get("videoId")]

    def _videos(self, ids: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(ids), 50):
            resp = self.yt.videos().list(part="snippet,statistics,contentDetails",
                                         id=",".join(ids[i:i + 50]), maxResults=50).execute()
            self.units += 1
            out.update({it["id"]: it for it in resp.get("items", [])})
        return out

    def _subscribers(self, channel_ids: list[str]) -> dict[str, int | None]:
        out: dict[str, int | None] = {}
        for i in range(0, len(channel_ids), 50):
            resp = self.yt.channels().list(part="statistics", id=",".join(channel_ids[i:i + 50]),
                                           maxResults=50).execute()
            self.units += 1
            for it in resp.get("items", []):
                st = it.get("statistics", {})
                out[it["id"]] = None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount"))
        return out

    def _categories(self) -> dict[str, str]:
        try:
            resp = self.yt.videoCategories().list(part="snippet", regionCode=self.s["region"]).execute()
            self.units += 1
            return {it["id"]: it["snippet"]["title"] for it in resp.get("items", [])}
        except Exception as exc:  # 카테고리 이름은 없어도 된다
            log.warning("카테고리 조회 실패: %s", exc)
            return {}

    def collect(self, now: datetime | None = None, on_status: Callable[[str], None] | None = None,
                cancel: threading.Event | None = None) -> list[TrendVideo]:
        now = now or utcnow()
        status = on_status or log.info

        def check():
            if cancel is not None and cancel.is_set():
                from .ollama_client import GenerationCancelled
                raise GenerationCancelled("사용자가 취소했습니다.")

        raw: dict[str, dict] = {}
        sources: dict[str, set[str]] = {}
        status("한국 인기 급상승 목록 가져오는 중…")
        for vid, it in self._popular().items():
            raw[vid] = it
            sources.setdefault(vid, set()).add("popular")
        search_ids: list[str] = []
        for q in self.s["search_queries"]:
            check()
            status(f"최근 {self.s['lookback_days']}일 인기 쇼츠 검색 중… ({q or '전체'})")
            for vid in self._search_ids(q, now):
                search_ids.append(vid)
                sources.setdefault(vid, set()).add(f"search:{q or '전체'}")
        check()
        missing = [v for v in dict.fromkeys(search_ids) if v not in raw]
        raw.update(self._videos(missing))

        cutoff = now - timedelta(days=float(self.s["lookback_days"]))
        max_sec = int(self.s["shorts_max_seconds"])
        vids = []
        for vid, it in raw.items():
            sn, st = it.get("snippet", {}), it.get("statistics", {})
            if sn.get("liveBroadcastContent", "none") != "none":
                continue
            published = from_iso(sn["publishedAt"])
            duration = parse_duration(it.get("contentDetails", {}).get("duration"))
            views = _int(st.get("viewCount")) or 0
            if duration is None or duration > max_sec or published < cutoff or published > now:
                continue
            if views < int(self.s["min_views"]):
                continue
            title = sn.get("title", "")
            if not title_matches(title, self.s.get("title_language", "any")):
                continue
            vids.append(TrendVideo(vid, title, sn.get("channelId", ""), sn.get("channelTitle", ""),
                                   published, duration, views, _int(st.get("likeCount")),
                                   _int(st.get("commentCount")), category=sn.get("categoryId", ""),
                                   tags=list(sn.get("tags") or [])[:8], sources=sources.get(vid, set())))
        check()
        if vids:
            status(f"채널 {len({v.channel_id for v in vids})}개 구독자 수 확인 중…")
            subs = self._subscribers(sorted({v.channel_id for v in vids}))
            cats = self._categories()
            for v in vids:
                v.subscribers = subs.get(v.channel_id)
                v.category = cats.get(v.category, v.category)
        vids.sort(key=lambda v: v.views_per_hour(now), reverse=True)
        status(f"유행 쇼츠 {len(vids)}개 (쿼터 약 {self.units} unit 사용)")
        return vids


# ---- 분석 텍스트 -------------------------------------------------------------------

def _compact(n: float | None, lang: str = "ko") -> str:
    if n is None:
        return "-"
    if lang == "en":
        for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if n >= div:
                return f"{n / div:.1f}{unit}"
        return fnum(n)
    if n >= 1e8:
        return f"{n / 1e8:.1f}억"
    if n >= 1e4:
        return f"{n / 1e4:.1f}만"
    return fnum(n)


def hot_keywords(videos: list[TrendVideo], n: int = 15) -> list[tuple[str, int]]:
    """제목·해시태그·태그에서 여러 영상에 반복되는 단어 (영상당 1번만 셈)."""
    counter: Counter[str] = Counter()
    for v in videos:
        words = set()
        for tok in re.findall(r"#?[0-9A-Za-z가-힣]+", v.title + " " + " ".join(v.tags)):
            w = tok.lstrip("#")
            if re.search(r"[가-힣]", w) and len(w) > 2:
                w = _JOSA.sub("", w)
            if len(w) >= 2 and not w.isdigit() and w.lower() not in _STOP:
                words.add(w.lower() if w.isascii() else w)
        counter.update(words)
    return [(w, c) for w, c in counter.most_common(n) if c >= 2]


_LABELS = {
    "ko": dict(head="분석 시각: {t} / 지역: {r} / 최근 {d}일 / 쇼츠({m}초 이하) {n}개 중 상위 {k}개",
               top="[시간당 조회수 상위 쇼츠]", views="조회", vph="시간당", subs="구독자",
               breakout=" (구독자의 {b:.0f}배 → 떡상)", sec="초", kw="[여러 영상에 반복되는 키워드]",
               cat="[카테고리 분포]", cat_n="{c} {n}개", small="[작은 채널인데 알고리즘을 탄 영상 {n}개]"),
    "en": dict(head="Analyzed: {t} / Region: {r} / Last {d} days / Top {k} of {n} Shorts (<= {m}s)",
               top="[Shorts ranked by views per hour]", views="views", vph="per hour", subs="subs",
               breakout=" ({b:.0f}x subs -> breakout)", sec="s", kw="[Keywords repeated across videos]",
               cat="[Categories]", cat_n="{c} {n}", small="[{n} breakout videos from small channels]"),
}


def trend_context(videos: list[TrendVideo], now: datetime, tz: ZoneInfo, settings: dict | None = None) -> str:
    """Ollama 프롬프트에 넣을 유행 요약 (영어 모드면 영어로)."""
    s = {**DEFAULT_TRENDS, **(settings or {})}
    lang = "en" if s.get("language") == "en" else "ko"
    L = _LABELS[lang]
    c = lambda n: _compact(n, lang)  # noqa: E731
    ratio = float(s["breakout_ratio"])
    top = videos[: int(s["top_n"])]
    lines = [L["head"].format(t=f"{now.astimezone(tz):%Y-%m-%d %H:%M}", r=s["region"], d=s["lookback_days"],
                              m=s["shorts_max_seconds"], n=len(videos), k=len(top)), "", L["top"]]
    for i, v in enumerate(top, 1):
        b = v.breakout
        sub = f"{L['subs']} {c(v.subscribers)}" + (L["breakout"].format(b=b) if b and b >= ratio else "")
        tags = " ".join("#" + t for t in v.tags[:4])
        lines.append(f"{i}. {v.title} | {L['views']} {c(v.views)} | {L['vph']} {c(v.views_per_hour(now))} | "
                     f"{sub} | {v.category or '-'} | {v.duration}{L['sec']}" + (f" | {tags}" if tags else ""))
    kws = hot_keywords(top)
    if kws:
        lines += ["", L["kw"], ", ".join(f"{w}({n})" for w, n in kws)]
    cats = Counter(v.category or "-" for v in top).most_common(5)
    lines += ["", L["cat"], ", ".join(L["cat_n"].format(c=cat, n=n) for cat, n in cats)]
    breakout = [v for v in top if v.breakout and v.breakout >= ratio]
    if breakout:
        lines += ["", L["small"].format(n=len(breakout))]
        lines += [f"- {v.title} ({L['subs']} {c(v.subscribers)}, {L['views']} {c(v.views)})" for v in breakout[:8]]
    return "\n".join(lines)


def trend_table(videos: list[TrendVideo], now: datetime, tz: ZoneInfo, limit: int = 50,
                breakout_ratio: float = 3.0) -> str:
    """GUI '트렌드' 탭에 보여줄 표."""
    lines = [f"{'순위':>3}  {'시간당':>7}  {'조회수':>7}  {'구독자':>7}  게시        제목"]
    for i, v in enumerate(videos[:limit], 1):
        mark = "🔥" if v.breakout and v.breakout >= breakout_ratio else "  "
        lines.append(f"{i:>3}. {_compact(v.views_per_hour(now)):>7}  {_compact(v.views):>7}  "
                     f"{_compact(v.subscribers):>7}  {v.published_at.astimezone(tz):%m-%d %H시}  {mark}{v.title}")
    lines += ["", "시간당 = 게시 후 시간당 평균 조회수 · 🔥 = 구독자 수보다 조회수가 훨씬 많은 '떡상' 영상"]
    return "\n".join(lines)


# ---- 전체 흐름 -------------------------------------------------------------------

@dataclass
class TrendResult:
    videos: list[TrendVideo]
    context: str
    table: str
    generation: object | None = None     # generator.Generation
    units: int = 0


def run_trends(cfg, *, service=None, generator=None, generate: bool = True, now: datetime | None = None,
               on_status: Callable[[str], None] | None = None, on_token: Callable[[str], None] | None = None,
               cancel: threading.Event | None = None) -> TrendResult:
    """유행 쇼츠 수집 → (generate=True면) 주제/쇼츠 대본 생성 + outputs/날짜/트렌드.md 저장."""
    from .collector import build_youtube_service
    from .generator import ScriptGenerator, ensure_prompts, record_generation, save_generation

    now = now or utcnow()
    tz = ZoneInfo(cfg.schedule["timezone"])
    settings = {**DEFAULT_TRENDS, **(cfg.raw.get("trends") or {})}
    status = on_status or log.info
    collector = TrendCollector(service or build_youtube_service(cfg.youtube_api_key), settings)
    videos = collector.collect(now, status, cancel)
    if not videos:
        raise TrendError("조건에 맞는 최근 유행 쇼츠를 찾지 못했습니다. config.yaml 의 trends 설정"
                         "(lookback_days, min_views 등)을 넓혀 보세요.")
    context = trend_context(videos, now, tz, settings)
    table = trend_table(videos, now, tz, breakout_ratio=float(settings["breakout_ratio"]))
    result = TrendResult(videos, context, table, units=collector.units)
    if not generate:
        return result

    ensure_prompts(cfg)
    gen = generator or ScriptGenerator.from_config(cfg)
    g = gen.generate(channel_id=TREND_CHANNEL_ID, channel_title=TREND_TITLE, context=context, now=now,
                     trigger="trend", topics_prompt=TREND_TOPICS_PROMPT, script_prompt=SHORTS_SCRIPT_PROMPT,
                     script_minutes=float(settings["script_seconds"]) / 60,
                     on_status=on_status, on_token=on_token, cancel=cancel)
    save_generation(g, cfg.outputs_dir, tz)
    from .db import Database

    with Database(cfg.db_path) as db:
        record_generation(db, g)
    result.generation = g
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from .config import load_config
    from .paths import default_config_path

    parser = argparse.ArgumentParser(description="최근 유행 쇼츠 분석")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--generate", action="store_true", help="주제/쇼츠 대본까지 생성 (Ollama)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config(Path(args.config))
    stream = (lambda t: (sys.stdout.write(t), sys.stdout.flush())) if args.generate else None
    res = run_trends(cfg, generate=args.generate, on_status=lambda m: print(f"▶ {m}"), on_token=stream)
    print("\n" + res.table)
    if res.generation is not None:
        print(f"\n✅ 저장: {res.generation.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
