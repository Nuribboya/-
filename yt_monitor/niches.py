"""콘텐츠 장르(니치): 무엇을 유행으로 볼지 · 어떤 주제를 고를지 · 어떤 화면을 쓸지.

general  : 지역 전체 인기 급상승 + 조회수 상위 쇼츠 (예전 방식)
extreme  : 액션 스포츠 — 고른 종목(산악자전거 · 파쿠르 · 스노보드 · 다이빙 · 패러글라이딩 …)만
           - 종목마다 검색어 1개로 그 종목 쇼츠를 모으고, 인기 급상승은 스포츠 카테고리에서 종목 키워드가 있는 것만
           - 주제/대본은 반드시 고른 종목 안에서 (라이딩 · 묘기 · 아찔한 순간 · 기록 · 프로의 비밀)
           - 화면은 그 종목의 액션 스톡 영상(POV · 슬로 모션 · 드론) + 관중

trends.niche 로 장르를, trends.sports 로 종목을 고른다 (설정 창에서 체크).
장르 값(search_queries, lookback_days …)은 config의 같은 키보다 우선한다.
"""

from __future__ import annotations

import re

# 액션 스포츠 종목: 설정에서 체크한 것만 쓴다. 검색 1개 = 쿼터 100.
SPORTS: dict[str, dict] = {
    "mtb": {"label": "산악자전거", "en": "mountain biking (downhill / enduro / freeride)",
            "query": "mountain bike downhill",
            "keywords": ["mtb", "mountain ?bik", "downhill", "enduro", "bike ?park", "freeride", "dirt ?jump"],
            "stock": ["mountain bike downhill", "mountain biking forest trail", "mtb jump", "mountain biker POV"]},
    "parkour": {"label": "파쿠르", "en": "parkour / free running",
                "query": "parkour",
                "keywords": ["parkour", "free ?run", "rooftop", "freerunn"],
                "stock": ["parkour jump", "free running rooftop", "parkour city", "parkour flip"]},
    "snowboard": {"label": "스노보드", "en": "snowboarding",
                  "query": "snowboarding",
                  "keywords": ["snowboard", "backcountry", "halfpipe", "slopestyle", "big air"],
                  "stock": ["snowboarding powder", "snowboard jump", "snowboarder mountain", "snowboard trick"]},
    "diving": {"label": "다이빙 (절벽 · 하이다이빙)", "en": "cliff diving / high diving",
               "query": "cliff diving",
               "keywords": ["cliff ?div", "high ?div", "diving", "diver"],
               "stock": ["cliff diving", "high diving", "diving into ocean", "cliff jump water"]},
    "paragliding": {"label": "패러글라이딩", "en": "paragliding / speed flying",
                    "query": "paragliding",
                    "keywords": ["paraglid", "paramotor", "speed ?fl", "hang ?glid", "acro"],
                    "stock": ["paragliding", "paraglider flying mountains", "paragliding sunset", "paragliding POV"]},
    "skydiving": {"label": "스카이다이빙", "en": "skydiving",
                  "query": "skydiving",
                  "keywords": ["skydiv", "free ?fall", "parachute", "canopy"],
                  "stock": ["skydiving freefall", "skydivers formation", "parachute opening"]},
    "wingsuit": {"label": "윙슈트 · 베이스점프", "en": "wingsuit / BASE jumping",
                 "query": "wingsuit",
                 "keywords": ["wingsuit", "base ?jump", "proximity"],
                 "stock": ["wingsuit flying", "base jumping cliff"]},
    "ski": {"label": "스키", "en": "freeride skiing",
            "query": "freeride skiing",
            "keywords": ["ski", "freeride", "powder", "avalanche"],
            "stock": ["freeride skiing powder", "ski jump mountain"]},
    "surf": {"label": "서핑", "en": "big wave surfing",
             "query": "big wave surfing",
             "keywords": ["surf", "big wave", "barrel", "nazar"],
             "stock": ["big wave surfing", "surfing barrel wave"]},
    "bmx": {"label": "BMX · 스케이트보드", "en": "BMX / skateboarding",
            "query": "bmx tricks",
            "keywords": ["bmx", "skate", "skatepark", "kickflip"],
            "stock": ["bmx trick skatepark", "skateboarding trick"]},
    "motocross": {"label": "모토크로스", "en": "motocross / FMX",
                  "query": "motocross",
                  "keywords": ["motocross", "fmx", "dirt ?bike", "supercross"],
                  "stock": ["motocross jump", "dirt bike race"]},
    "climbing": {"label": "암벽등반", "en": "rock climbing / free solo",
                 "query": "rock climbing",
                 "keywords": ["climb", "free ?solo", "boulder"],
                 "stock": ["rock climbing cliff", "climber mountain wall"]},
}
DEFAULT_SPORTS = ["mtb", "parkour", "snowboard", "diving", "paragliding"]

NICHES: dict[str, dict] = {
    "general": {
        "label": "일반 유행 (급상승 전체)",
    },
    "extreme": {
        "label": "액션 스포츠 (아래에서 종목 선택)",
        "popular_category": "17",          # 인기 급상승은 스포츠 카테고리만
        "popular_pages": 2,
        "lookback_days": 7,                # 종목 쇼츠는 수가 적어서 1주일
        "min_views": 20000,
        # 현장감: 관중 리액션 컷 (스톡 영상 검색어) · 관중 함성(sfx/crowd 폴더) · AI 이미지 스타일
        "crowd_queries": ["crowd cheering", "spectators cheering", "crowd watching event", "people filming with phones",
                          "audience shocked", "stadium crowd"],
        "crowd_sfx": True,
        "suspense": True,                  # 긴장감 연출: 컷 가속 · 클라이맥스 슬로 모션 · 심장 박동 · 임팩트
        "image_style": ("action sports photography, telephoto lens, spectators in the background, real photo, "
                        "motion blur, natural light"),
    },
}

DEFAULT_NICHE = "extreme"
NICHE_KEYS = ("search_queries", "popular_category", "popular_pages", "lookback_days", "min_views", "keywords")


def selected_sports(settings: dict | None) -> list[str]:
    picked = [s for s in ((settings or {}).get("sports") or []) if s in SPORTS]
    return picked or list(DEFAULT_SPORTS)


def _sports_list_en(keys: list[str]) -> str:
    return ", ".join(SPORTS[k]["en"] for k in keys)


def _sports_list_ko(keys: list[str]) -> str:
    return ", ".join(SPORTS[k]["label"] for k in keys)


def niche_of(settings: dict | None) -> dict:
    """장르 정의 (액션 스포츠면 고른 종목으로 검색어 · 키워드 · 지시문을 채운다)."""
    name = (settings or {}).get("niche") or DEFAULT_NICHE
    base = NICHES.get(name, NICHES["general"])
    if name != "extreme":
        return base
    keys = selected_sports(settings)
    n = dict(base)
    n["sports"] = keys
    n["search_queries"] = [SPORTS[k]["query"] for k in keys]
    n["keywords"] = [kw for k in keys for kw in SPORTS[k]["keywords"]]
    en, ko = _sports_list_en(keys), _sports_list_ko(keys)
    n["focus_en"] = (
        "[Channel niche: ACTION SPORTS]\n"
        f"This channel ONLY makes Shorts about these sports: {en}.\n"
        "- Every topic must be about one of these sports, even if other trends appear in the data. "
        "No storms, animals, disasters or unrelated stunts.\n"
        "- Good angles: one insane run/line/trick and what makes it so hard, the closest call in that sport, "
        "the first time someone landed X, how pros pull off Y, POV 'you are the rider', records, "
        "'rookie vs pro', the hidden danger most viewers don't notice.\n"
        "- It is told as narration over action stock footage of that sport (no need for the original clips).\n"
        "- Make the heart race: line 1 = what's at stake right now ('One wrong move at 70 km/h and it's over.'), "
        "then the run/flight/jump second by second, rising stakes, very short lines and a countdown near the "
        "climax, hold the outcome one more line, then the landing or the twist. Never reveal it early.\n"
        "- Use an anonymous rider ('a rider', 'a diver') unless it is a famous, well-documented record; "
        "don't invent precise facts about real people. Never tell viewers to try it themselves, no gore."
    )
    n["focus_ko"] = (
        "[채널 장르: 액션 스포츠]\n"
        f"이 채널은 다음 종목의 쇼츠만 만든다: {ko}.\n"
        "- 데이터에 다른 유행이 있어도 주제는 반드시 이 종목 중 하나. 폭풍 · 동물 · 재난 · 관계없는 묘기는 금지.\n"
        "- 좋은 방향: 미친 라인/묘기 하나와 그게 왜 어려운지, 그 종목에서 가장 아찔했던 순간, 처음 성공한 기술, "
        "프로가 하는 법, '당신이 라이더라면' 1인칭, 기록, '초보 vs 프로', 사람들이 못 보는 숨은 위험.\n"
        "- 그 종목의 액션 스톡 영상 위 내레이션.\n"
        "- 심장이 뛰게: 첫 줄 = 지금 걸린 것 ('시속 70km, 한 번만 실수하면 끝.'), 라이딩/비행/점프를 초 단위로, "
        "점점 커지는 위험, 클라이맥스 직전 아주 짧은 문장과 카운트다운, 결과를 한 줄 더 미룬 뒤 착지나 반전.\n"
        "- 주인공은 익명. 실존 인물에 대한 정확한 사실을 지어내지 말 것. 따라 하라고 하지 말 것."
    )
    stock = "; ".join(", ".join(SPORTS[k]["stock"]) for k in keys)
    n["visual_en"] = (
        f"This Short is about {en}. EVERY search keyword must show that sport in action "
        f"(examples: {stock}). Prefer POV helmet cam, slow motion, drone shots of the rider. "
        "No storms, animals, office or calm footage. AI image prompts: the same sport, dramatic real-looking action "
        "moment at an event with spectators, motion blur, low angle."
    )
    return n


def apply_niche(settings: dict) -> dict:
    """trends 설정 + 장르 값 (장르 값이 우선)."""
    n = niche_of(settings)
    out = dict(settings)
    for k in NICHE_KEYS:
        if k in n:
            out[k] = list(n[k]) if isinstance(n[k], list) else n[k]
    return out


def matches_niche(title: str, tags: list[str] | None, keywords: list[str] | None) -> bool:
    if not keywords:
        return True
    text = " ".join([title or "", *(tags or [])]).lower()
    return any(re.search(r"\b" + k, text) for k in keywords)


def focus_text(settings: dict | None, lang: str = "en") -> str:
    n = niche_of(settings)
    return n.get("focus_en" if lang == "en" else "focus_ko", "")


def visual_text(settings: dict | None) -> str:
    return niche_of(settings).get("visual_en", "")


def label_of(settings: dict | None) -> str:
    n = niche_of(settings)
    if n.get("sports"):
        return "액션 스포츠: " + _sports_list_ko(n["sports"])
    return n["label"]
