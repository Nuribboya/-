"""업로드 정보 생성: 클릭 잘 되는 제목 후보 · 카테고리 · 설명 · 해시태그 · 태그.

- 유행 쇼츠 분석을 거친 경우엔 "카테고리별 시간당 조회수" 통계를 같이 넘겨서 근거 있는 카테고리를 고른다.
- Ollama가 실패하거나 JSON이 깨져도 영상/대본 생성은 막지 않는다 (fallback 값으로 채움).
- 결과는 대본 기획안(.md)과 영상 옆 `<파일명>_업로드정보.txt` 에 복사해서 붙여넣기 좋게 저장된다.
"""

from __future__ import annotations

import logging
import re
import statistics
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

UPLOAD_PROMPT = "upload_meta.txt"

# 업로드할 때 고를 수 있는 YouTube 카테고리 (id: (영문 이름, 한국어 이름))
CATEGORIES: dict[str, tuple[str, str]] = {
    "1": ("Film & Animation", "영화/애니메이션"),
    "2": ("Autos & Vehicles", "자동차"),
    "10": ("Music", "음악"),
    "15": ("Pets & Animals", "애완동물/동물"),
    "17": ("Sports", "스포츠"),
    "19": ("Travel & Events", "여행/이벤트"),
    "20": ("Gaming", "게임"),
    "22": ("People & Blogs", "인물/블로그"),
    "23": ("Comedy", "코미디"),
    "24": ("Entertainment", "엔터테인먼트"),
    "25": ("News & Politics", "뉴스/정치"),
    "26": ("Howto & Style", "노하우/스타일"),
    "27": ("Education", "교육"),
    "28": ("Science & Technology", "과학기술"),
}
DEFAULT_CATEGORY = "24"
_NAME_TO_ID = {name.casefold(): cid for cid, names in CATEGORIES.items() for name in names}

CATEGORY_NOTE = ("※ 카테고리는 조회수를 크게 좌우하지는 않고, 비슷한 영상과 묶여 추천되는 데 조금 도움이 됩니다. "
                 "조회수에는 제목 · 첫 2초 훅 · 끝까지 보는 비율이 훨씬 중요해요.")


def category_id(value) -> str | None:
    """"24", 24, "Entertainment", "엔터테인먼트" → "24"."""
    if value is None:
        return None
    s = str(value).strip()
    if s in CATEGORIES:
        return s
    m = re.match(r"^(\d+)", s)
    if m and m.group(1) in CATEGORIES:
        return m.group(1)
    return _NAME_TO_ID.get(s.casefold())


def category_label(cid: str) -> str:
    en, ko = CATEGORIES.get(cid, CATEGORIES[DEFAULT_CATEGORY])
    return f"{ko} ({en})"


# ---- 유행 데이터 → 카테고리 통계 ------------------------------------------------------

@dataclass
class CategoryStat:
    category_id: str
    name: str
    count: int
    median_vph: float          # 시간당 조회수 중앙값


def category_stats(videos, now: datetime, top_n: int = 50) -> list[CategoryStat]:
    """유행 쇼츠(TrendVideo) 목록 → 카테고리별 개수/시간당 조회수 중앙값 (많이 뜬 순)."""
    groups: dict[str, list[float]] = defaultdict(list)
    names: dict[str, str] = {}
    for v in videos[:top_n]:
        cid = category_id(getattr(v, "category_id", "") or "") or category_id(v.category) or ""
        if not cid:
            continue
        groups[cid].append(v.views_per_hour(now))
        names[cid] = v.category or CATEGORIES.get(cid, ("?",))[0]
    stats = [CategoryStat(cid, names[cid], len(vph), statistics.median(vph)) for cid, vph in groups.items()]
    return sorted(stats, key=lambda s: (s.count, s.median_vph), reverse=True)


def category_hint(stats: list[CategoryStat], lang: str = "ko") -> str:
    if not stats:
        return ""
    if lang == "en":
        head = "[Categories of the trending Shorts: count, median views/hour]"
        rows = [f"- {s.category_id} {CATEGORIES.get(s.category_id, (s.name,))[0]}: {s.count} videos, "
                f"{s.median_vph:,.0f}/h" for s in stats[:8]]
    else:
        head = "[유행 쇼츠의 카테고리: 영상 수, 시간당 조회수 중앙값]"
        rows = [f"- {s.category_id} {category_label(s.category_id)}: {s.count}개, 시간당 {s.median_vph:,.0f}회"
                for s in stats[:8]]
    return "\n".join([head, *rows])


# ---- 결과 ------------------------------------------------------------------------

@dataclass
class UploadMeta:
    titles: list[dict] = field(default_factory=list)     # [{"title", "why"}]
    best: int = 0
    category_id: str = DEFAULT_CATEGORY
    category_reason: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "ollama"                              # ollama | fallback

    @property
    def title(self) -> str:
        return self.titles[self.best]["title"] if self.titles else ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "UploadMeta | None":
        if not d:
            return None
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def _clean_tag(t: str) -> str:
    return re.sub(r"[^\w]", "", str(t).lstrip("#"))


def _hashtags(items, limit: int = 5) -> list[str]:
    out: list[str] = []
    for t in items or []:
        t = _clean_tag(t)
        if t and t.casefold() not in {o[1:].casefold() for o in out}:
            out.append("#" + t)
    if "#shorts" not in {o.casefold() for o in out}:
        out.insert(0, "#Shorts")
    return out[:limit]


def parse_upload_meta(data, *, lang: str, fallback_titles: list[str], max_title: int = 70) -> UploadMeta:
    """모델 JSON → UploadMeta. 키 이름이 조금 달라도, 값이 비어도 최대한 살린다."""
    from .generator import _first, has_hangul

    data = data if isinstance(data, dict) else {}
    raw_titles = _first(data, "titles", "제목", "title_options", default=[]) or []
    if isinstance(raw_titles, (str, dict)):
        raw_titles = [raw_titles]
    try:
        best_raw = int(_first(data, "best", "추천", default=1)) - 1
    except (TypeError, ValueError):
        best_raw = 0
    titles: list[dict] = []
    best = 0
    for i, t in enumerate(raw_titles):
        if isinstance(t, dict):
            title, why = str(_first(t, "title", "제목", default="")).strip(), str(_first(t, "why", "reason", "이유",
                                                                                           default="")).strip()
        else:
            title, why = str(t).strip(), ""
        title = title.strip("\"'“”")[:max_title].strip()
        if not title or (lang == "en" and has_hangul(title)):
            continue
        if title.casefold() not in {x["title"].casefold() for x in titles}:
            if i == best_raw:               # 한국어 제목이 빠져도 모델이 추천한 제목을 그대로 가리키게
                best = len(titles)
            titles.append({"title": title, "why": why})
    for t in fallback_titles:                       # 모델이 제목을 못 줬으면 주제 단계의 제목 후보로
        if len(titles) >= 3:
            break
        if t and not (lang == "en" and has_hangul(t)) and t.casefold() not in {x["title"].casefold() for x in titles}:
            titles.append({"title": t[:max_title], "why": ""})

    cid = category_id(_first(data, "category_id", "category", "카테고리", default=None)) or DEFAULT_CATEGORY
    desc = str(_first(data, "description", "설명", default="")).strip()
    hashtags = _hashtags(_first(data, "hashtags", "해시태그", default=[]))
    tags = [str(t).strip().lstrip("#") for t in (_first(data, "tags", "태그", default=[]) or []) if str(t).strip()]
    return UploadMeta(titles, best, cid, str(_first(data, "category_reason", "카테고리_이유", "reason",
                                                    default="")).strip(),
                      desc, hashtags, tags[:15])


def fallback_meta(title: str, topic: dict | None, stats: list[CategoryStat], lang: str,
                  script: str = "") -> UploadMeta:
    first = re.split(r"(?<=[.!?])\s+", script.strip().splitlines()[0])[0] if script.strip() else ""
    titles = [title, *((topic or {}).get("titles") or [])]
    meta = parse_upload_meta({}, lang=lang, fallback_titles=[t for t in titles if t])
    if not meta.titles and first:             # 영어 모드인데 제목이 한국어뿐 → 대본 첫 문장
        meta.titles = [{"title": first[:70], "why": ""}]
    if stats:
        meta.category_id = stats[0].category_id
        meta.category_reason = f"유행 쇼츠에서 가장 많이 보인 카테고리 ({stats[0].count}개)"
    meta.source = "fallback"
    return meta


def generate_upload_meta(client, prompts_dir: Path, *, lang: str, title: str, script: str,
                         topic: dict | None = None, trend_hint: str = "", stats: list[CategoryStat] | None = None,
                         options: dict | None = None, cancel: threading.Event | None = None) -> UploadMeta:
    """Ollama로 업로드 정보 생성. 실패하면 fallback (예외를 올리지 않는다)."""
    from .generator import _extract_json, load_template, localized, render_template
    from .ollama_client import GenerationCancelled

    stats = stats or []
    try:
        template = load_template(prompts_dir, localized(UPLOAD_PROMPT, lang))
        cats = "\n".join(f"{cid}: {en}" for cid, (en, _ko) in CATEGORIES.items())
        prompt = render_template(template, {
            "title": title or (topic or {}).get("topic", ""),
            "topic": (topic or {}).get("topic", "") or title,
            "script": script,
            "categories": cats,
            "category_data": trend_hint or "(no trend data)",
        })
        text = client.chat(prompt, json_mode=True, options={"temperature": 0.7, **(options or {})}, cancel=cancel)
        fallback_titles = [title, *((topic or {}).get("titles") or [])]
        meta = parse_upload_meta(_extract_json(text), lang=lang, fallback_titles=[t for t in fallback_titles if t])
        if not meta.titles:
            raise ValueError("제목 후보 없음")
        return meta
    except (InterruptedError, GenerationCancelled):
        raise
    except Exception as exc:  # 업로드 정보는 부가 기능 → 실패해도 대본/영상은 계속
        log.warning("업로드 정보 생성 실패 (기본값 사용): %s", exc)
        return fallback_meta(title, topic, stats, lang, script)


# ---- 출력 ------------------------------------------------------------------------

def render_upload_text(meta: UploadMeta, credits: str = "", bgm_note: str = "") -> str:
    """업로드할 때 복사해서 붙여넣기 좋은 텍스트 (.txt / GUI)."""
    lines = ["📋 업로드 정보 (YouTube Shorts)", "", "■ 제목 후보 (⭐ = 추천, 클릭 잘 되는 순)"]
    for i, t in enumerate(meta.titles):
        star = "⭐ " if i == meta.best else "   "
        lines.append(f"{star}{i + 1}) {t['title']}  ({len(t['title'])}자)")
        if t.get("why"):
            lines.append(f"      └ {t['why']}")
    lines += ["", f"■ 카테고리: {category_label(meta.category_id)}"]
    if meta.category_reason:
        lines.append(f"   └ {meta.category_reason}")
    lines.append(f"   {CATEGORY_NOTE}")
    desc = meta.description.strip()
    tags_line = " ".join(meta.hashtags)
    body = (desc + ("\n\n" if desc else "") + tags_line).strip()
    if credits.strip():
        body += "\n\n" + credits.strip()
    if bgm_note:
        lines += ["", "■ 배경음악", f"   {bgm_note}"]
    lines += ["", "■ 설명 (그대로 복사)", "-" * 40, body, "-" * 40]
    if meta.tags:
        lines += ["", "■ 태그 (고급 설정 → 태그 칸, 쉼표로 구분)", ", ".join(meta.tags)]
    lines += ["", "■ 업로드 체크리스트",
              "- 제목은 40자 안쪽이 휴대폰에서 잘리지 않아요.",
              "- AI 이미지가 들어갔다면 '변경되거나 합성된 콘텐츠' 항목을 '예'로 표시하세요.",
              *([] if credits.strip() else ["- 스톡 영상을 썼다면 출처(_출처.txt)를 설명 맨 아래에 붙여 넣으세요."])]
    if meta.source == "fallback":
        lines += ["", "(Ollama 응답이 없어 기본값으로 채웠습니다. 제목/설명을 직접 다듬어 주세요.)"]
    return "\n".join(lines)


def render_upload_md(meta: UploadMeta) -> list[str]:
    out = ["## 📋 업로드 정보", "", "- 제목 후보 (⭐ 추천):"]
    for i, t in enumerate(meta.titles):
        out.append(f"  {i + 1}) {'⭐ ' if i == meta.best else ''}{t['title']}" + (f" — {t['why']}" if t.get("why") else ""))
    out.append(f"- 카테고리: {category_label(meta.category_id)}" +
               (f" — {meta.category_reason}" if meta.category_reason else ""))
    out.append(f"- 해시태그: {' '.join(meta.hashtags)}")
    if meta.tags:
        out.append(f"- 태그: {', '.join(meta.tags)}")
    out += ["", "설명:", "", "```text", meta.description or "-", "```", "", CATEGORY_NOTE, ""]
    return out
