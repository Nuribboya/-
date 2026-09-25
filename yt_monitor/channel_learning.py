"""내 채널 학습: 내가 올린 영상 중 무엇이 잘 됐는지 보고 다음 영상에 반영한다.

1. 성과: 영상마다 "게시 후 48시간 조회수"(수집 시계열을 보간, 게시 시점 0회)를 구하고
   채널 중앙값으로 나눈 상대 점수를 쓴다 (1.0 = 보통, 2.0 = 평소의 2배).
   아직 48시간이 안 지났거나 그 뒤에 수집한 기록이 없는 영상은 제외.
2. 연결: 이 프로그램이 만든 영상 기록(productions: 제목 후보 · 분위기 · 음성 · 길이 · 배경음악)을
   실제로 올라간 영상과 제목으로 연결한다 (제목 후보와 비슷하면 같은 영상).
3. 비교: 길이 · 제목 패턴 · 올린 시간 · 분위기 · 음성별 점수, 잘 된 영상/안 된 영상 제목
4. 반영: 대본 목표 길이(유행 분석값과 평균), 주제/대본/제목 프롬프트, 추천 업로드 시간

영상이 min_videos(기본 6)개 이상 쌓여야 켜진다. 딥러닝으로 모델을 새로 학습시키는 방식이 아니라,
내 채널 데이터로 다음 영상의 선택을 바꾸는 피드백 방식이다.
"""

from __future__ import annotations

import difflib
import logging
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .analyzer import value_at

log = logging.getLogger(__name__)

DEFAULT_LEARNING = {
    "enabled": True,
    "min_videos": 6,            # 이만큼 쌓여야 반영 (그 전에는 리포트만)
    "settle_hours": 48,         # 게시 후 이 시간의 조회수로 비교
    "max_videos": 60,           # 최근 영상 몇 개까지 볼지
    "refresh_hours": 6,         # 원클릭 전에 내 채널 조회수가 이보다 오래됐으면 새로 수집 (쿼터 몇 unit)
    "weight": 0.5,              # 길이: 내 채널 값과 유행 값을 섞는 비율 (1이면 내 채널만)
}


@dataclass
class MyVideo:
    video_id: str
    title: str
    published_at: datetime
    duration: int | None
    views_48h: float
    score: float = 0.0                     # 채널 중앙값 대비
    production: dict | None = None         # 이 프로그램이 만든 영상이면 제작 기록


@dataclass
class MyInsights:
    n: int = 0
    min_videos: int = 6
    matched: int = 0
    videos: list[MyVideo] = field(default_factory=list)
    best_seconds: int | None = None
    duration_rows: list[dict] = field(default_factory=list)
    patterns: list[dict] = field(default_factory=list)       # {ko, en, count, lift}
    hours: list[dict] = field(default_factory=list)          # {start, count, score} (내 시간대)
    moods: list[dict] = field(default_factory=list)          # {name, count, score}
    voices: list[dict] = field(default_factory=list)
    winners: list[str] = field(default_factory=list)
    flops: list[str] = field(default_factory=list)
    upload_times: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.n >= self.min_videos


def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", " ", t or "").strip().casefold()


def _similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b or (len(a) > 12 and (a in b or b in a)):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_productions(videos: list[MyVideo], productions: list[dict], threshold: float = 0.75) -> int:
    """올라간 영상 ↔ 만든 영상 기록을 제목으로 연결 (만든 뒤 14일 안에 올라간 것만)."""
    used: set[int] = set()
    n = 0
    for v in videos:
        best, best_sim = None, threshold
        for p in productions:
            if p["id"] in used or not (p["created_at"] - timedelta(hours=1) <= v.published_at
                                       <= p["created_at"] + timedelta(days=14)):
                continue
            sim = max(_similar(v.title, t) for t in [p["title"], *p["titles"]])
            if sim >= best_sim:
                best, best_sim = p, sim
        if best is not None:
            used.add(best["id"])
            v.production = best
            n += 1
    return n


def _groups(videos: list[MyVideo], key, min_count: int = 2) -> list[dict]:
    g: dict = {}
    for v in videos:
        k = key(v)
        if k not in (None, ""):
            g.setdefault(k, []).append(v.score)
    rows = [{"name": k, "count": len(x), "score": statistics.median(x)} for k, x in g.items() if len(x) >= min_count]
    return sorted(rows, key=lambda r: (r["score"], r["count"]), reverse=True)


DUR_BUCKETS = [(0, 20, "20초 이하"), (21, 35, "21~35초"), (36, 50, "36~50초"), (51, 70, "51~70초"),
               (71, 10**6, "70초 초과")]


def collect_my_videos(db, channel_ids: list[str], now: datetime, settings: dict) -> list[MyVideo]:
    s = {**DEFAULT_LEARNING, **(settings or {})}
    settle = timedelta(hours=float(s["settle_hours"]))
    out: list[MyVideo] = []
    for cid in channel_ids:
        for v in db.get_videos(cid, limit=int(s["max_videos"])):
            if now - v.published_at < settle:
                continue
            snaps = db.get_snapshots(v.video_id)
            points = [(v.published_at, 0.0)] + [(x.collected_at, x.view_count) for x in snaps]
            m = value_at(points, v.published_at + settle)
            if m is None:                      # 48시간 뒤 수집 기록이 없음
                continue
            out.append(MyVideo(v.video_id, v.title, v.published_at, v.duration_seconds, m))
    med = statistics.median([v.views_48h for v in out]) if out else 0
    for v in out:
        v.score = v.views_48h / med if med > 0 else 0.0
    return out


def learn(db, channel_ids: list[str], now: datetime, *, local_tz: ZoneInfo, settings: dict | None = None) -> MyInsights:
    """내 채널 영상 → MyInsights."""
    from .trend_insights import TITLE_PATTERNS

    s = {**DEFAULT_LEARNING, **(settings or {})}
    vids = collect_my_videos(db, channel_ids, now, s)
    ins = MyInsights(n=len(vids), min_videos=int(s["min_videos"]), videos=vids)
    if not vids:
        return ins
    try:
        ins.matched = match_productions(vids, db.get_productions())
    except Exception as exc:  # productions 테이블이 없던 예전 DB 등
        log.warning("제작 기록 연결 실패: %s", exc)

    ranked = sorted(vids, key=lambda v: v.score, reverse=True)
    k = max(1, len(ranked) // 3)
    ins.winners = [v.title for v in ranked[:min(k, 5)] if v.score > 1]
    ins.flops = [v.title for v in ranked[-min(k, 5):] if v.score < 1]

    # 길이: 점수 상위 1/3의 길이 중앙값
    with_dur = [v for v in ranked if v.duration]
    top = with_dur[:max(2, len(with_dur) // 3)]
    if len(top) >= 2:
        ins.best_seconds = int(round(statistics.median(v.duration for v in top) / 5) * 5)
    for lo, hi, label in DUR_BUCKETS:
        grp = [v.score for v in vids if v.duration and lo <= v.duration <= hi]
        if grp:
            ins.duration_rows.append({"label": label, "count": len(grp), "score": statistics.median(grp)})

    # 제목 패턴
    for key, ko, en, test in TITLE_PATTERNS:
        yes = [v.score for v in vids if test(v.title)]
        no = [v.score for v in vids if not test(v.title)]
        if len(yes) >= 2 and len(no) >= 2 and statistics.median(no) > 0:
            ins.patterns.append({"key": key, "ko": ko, "en": en, "count": len(yes),
                                 "lift": statistics.median(yes) / statistics.median(no)})
    ins.patterns.sort(key=lambda p: p["lift"], reverse=True)

    # 올린 시간 (내 시간대, 3시간 단위)
    ins.hours = [{"start": r["name"], "count": r["count"], "score": r["score"]}
                 for r in _groups(vids, lambda v: v.published_at.astimezone(local_tz).hour // 3 * 3)]
    for r in ins.hours[:2]:
        if r["score"] >= 1:
            ins.upload_times.append(f"{r['start']:02d}:00~{(r['start'] + 3) % 24:02d}:00 "
                                    f"(내 채널 기준 · 평소의 {r['score']:.1f}배)")

    # 이 프로그램이 만든 영상: 분위기 · 음성
    made = [v for v in vids if v.production]
    ins.moods = _groups(made, lambda v: v.production.get("mood"))
    ins.voices = _groups(made, lambda v: v.production.get("voice"))
    return ins


# ---- 반영 -------------------------------------------------------------------------

def blend_seconds(trend_seconds: float | None, mine: MyInsights | None, settings: dict | None = None,
                  lo: int = 20, hi: int = 58) -> float | None:
    s = {**DEFAULT_LEARNING, **(settings or {})}
    if mine is None or not mine.ready or not mine.best_seconds:
        return trend_seconds
    w = float(s["weight"])
    val = mine.best_seconds if trend_seconds is None else (w * mine.best_seconds + (1 - w) * trend_seconds)
    return float(min(hi, max(lo, round(val / 5) * 5)))


def prompt_text(mine: MyInsights, lang: str = "en") -> str:
    if mine is None or not mine.ready:
        return ""
    if lang == "en":
        lines = [f"[What worked on THIS channel — {mine.n} of my Shorts, views at 48h vs my median]"]
        if mine.winners:
            lines.append("- Did well: " + " | ".join(mine.winners))
        if mine.flops:
            lines.append("- Flopped: " + " | ".join(mine.flops))
        lines.append("- Prefer topics and angles similar to what did well; avoid what flopped.")
        for p in mine.patterns[:3]:
            if abs(p["lift"] - 1) >= 0.2:
                lines.append(f"- Titles with {p['en']}: x{p['lift']:.1f} views")
        if mine.best_seconds:
            lines.append(f"- My best length: about {mine.best_seconds} seconds")
        if mine.moods:
            lines.append(f"- Tone that works here: {mine.moods[0]['name']} (x{mine.moods[0]['score']:.1f})")
        return "\n".join(lines)
    lines = [f"[내 채널에서 잘 된 것 — 내 쇼츠 {mine.n}개, 48시간 조회수 기준]"]
    if mine.winners:
        lines.append("- 잘 된 영상: " + " | ".join(mine.winners))
    if mine.flops:
        lines.append("- 안 된 영상: " + " | ".join(mine.flops))
    lines.append("- 잘 된 영상과 비슷한 주제/방향을 우선하고, 안 된 쪽은 피할 것.")
    for p in mine.patterns[:3]:
        if abs(p["lift"] - 1) >= 0.2:
            lines.append(f"- 제목에 {p['ko']}: 조회수 x{p['lift']:.1f}")
    if mine.best_seconds:
        lines.append(f"- 잘 된 길이: 약 {mine.best_seconds}초")
    if mine.moods:
        lines.append(f"- 잘 먹힌 분위기: {mine.moods[0]['name']} (x{mine.moods[0]['score']:.1f})")
    return "\n".join(lines)


def title_hint(mine: MyInsights, lang: str = "en") -> str:
    if mine is None or not mine.ready:
        return ""
    good = [p for p in mine.patterns if p["lift"] >= 1.2]
    bad = [p for p in mine.patterns if p["lift"] <= 0.8]
    parts = []
    if lang == "en":
        if mine.winners:
            parts.append("My best titles so far: " + " | ".join(mine.winners[:3]) + ".")
        if good:
            parts.append("On my channel these work: " + ", ".join(f"{p['en']} (x{p['lift']:.1f})" for p in good[:3]) + ".")
        if bad:
            parts.append("These underperform: " + ", ".join(p["en"] for p in bad[:3]) + ".")
        return ("[My channel]\n" + " ".join(parts)) if parts else ""
    if mine.winners:
        parts.append("지금까지 잘 된 제목: " + " | ".join(mine.winners[:3]) + ".")
    if good:
        parts.append("내 채널에서 잘 되는 것: " + ", ".join(f"{p['ko']}(x{p['lift']:.1f})" for p in good[:3]) + ".")
    if bad:
        parts.append("잘 안 되는 것: " + ", ".join(p["ko"] for p in bad[:3]) + ".")
    return ("[내 채널]\n" + " ".join(parts)) if parts else ""


def report_ko(mine: MyInsights) -> str:
    """🔥 트렌드 탭 맨 위 '내 채널 학습' 리포트."""
    if mine is None:
        return ""
    if not mine.ready:
        return (f"📈 내 채널 학습: 데이터 모으는 중 ({mine.n}/{mine.min_videos}개) — 올린 쇼츠가 48시간 지나고 "
                f"[▶ 지금 체크하기]로 조회수가 수집되면 자동으로 반영됩니다.")
    L = [f"📈 내 채널 학습: 쇼츠 {mine.n}개 (이 프로그램으로 만든 영상 {mine.matched}개 연결) — 48시간 조회수, 평소 대비"]
    if mine.winners:
        L.append("   👍 잘 된 영상: " + " / ".join(t[:40] for t in mine.winners))
    if mine.flops:
        L.append("   👎 안 된 영상: " + " / ".join(t[:40] for t in mine.flops))
    if mine.duration_rows:
        L.append("   ⏱ 길이: " + " · ".join(f"{r['label']} x{r['score']:.1f}({r['count']})" for r in mine.duration_rows))
    if mine.patterns:
        L.append("   ✍ 제목: " + " · ".join(f"{p['ko']} x{p['lift']:.1f}" for p in mine.patterns[:4]))
    if mine.hours:
        L.append("   🕒 올린 시간: " + " · ".join(f"{r['start']:02d}~{(r['start'] + 3) % 24:02d}시 x{r['score']:.1f}"
                                            for r in mine.hours[:3]))
    if mine.moods:
        L.append("   🎭 분위기: " + " · ".join(f"{r['name']} x{r['score']:.1f}({r['count']})" for r in mine.moods))
    if mine.voices:
        L.append("   🎙 음성: " + " · ".join(f"{r['name']} x{r['score']:.1f}({r['count']})" for r in mine.voices))
    L.append("   → 주제 · 대본 · 제목 · 길이 · 업로드 시간에 반영합니다")
    return "\n".join(L)


# ---- 실행 -------------------------------------------------------------------------

def refresh_my_channels(cfg, now: datetime, *, service=None, status=None) -> int:
    """내 채널 조회수가 refresh_hours보다 오래됐으면 새로 수집 (채널당 쿼터 몇 unit)."""
    from .collector import YouTubeCollector, build_youtube_service
    from .db import Database, from_iso

    s = {**DEFAULT_LEARNING, **(cfg.raw.get("learning") or {})}
    done = 0
    with Database(cfg.db_path) as db:
        if not db.conn.execute("SELECT 1 FROM channels LIMIT 1").fetchone():
            return 0                 # 한 번도 [▶ 지금 체크하기]를 안 했으면 채널 ID를 모른다 → 리포트에서 안내
        row = db.conn.execute("SELECT MAX(collected_at) AS t FROM video_stats").fetchone()
        last = from_iso(row["t"]) if row and row["t"] else None
        if last is not None and now - last < timedelta(hours=float(s["refresh_hours"])):
            return 0
        try:
            collector = YouTubeCollector(service or build_youtube_service(cfg.youtube_api_key), db,
                                         max_videos=cfg.youtube["max_videos_per_channel"])
            for ch in cfg.channels:
                collector.collect_channel(ch, now)
                done += 1
            db.commit()
        except Exception as exc:  # 학습용 부가 수집 → 실패해도 영상 만들기는 계속
            log.warning("내 채널 조회수 수집 실패: %s", exc)
            if status:
                status(f"내 채널 조회수를 새로 가져오지 못했습니다 (저장된 기록으로 학습): {exc}")
    if done and status:
        status(f"내 채널 {done}개 조회수를 새로 가져왔습니다 (학습용)")
    return done


def load_my_insights(cfg, now: datetime, *, service=None, status=None, refresh: bool = True) -> MyInsights | None:
    from .db import Database

    s = {**DEFAULT_LEARNING, **(cfg.raw.get("learning") or {})}
    if not s["enabled"] or not cfg.channels:
        return None
    if refresh:
        refresh_my_channels(cfg, now, service=service, status=status)
    tz = ZoneInfo(cfg.schedule["timezone"])
    with Database(cfg.db_path) as db:
        ids = [r["channel_id"] for r in db.conn.execute("SELECT channel_id FROM channels")]
        return learn(db, ids, now, local_tz=tz, settings=s)
