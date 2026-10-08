"""콘텐츠 장르(니치): 무엇을 유행으로 볼지 · 어떤 주제를 고를지 · 어떤 화면을 쓸지.

general  : 지역 전체 인기 급상승 + 조회수 상위 쇼츠 (예전 방식)
extreme  : 익스트림 스포츠 · 아찔한 순간 · 대자연의 위력 같은 "도파민" 쇼츠만 모아서 분석
           - 검색어로 장르 쇼츠를 모으고, 인기 급상승은 스포츠 카테고리만 보고 키워드로 한 번 더 거른다
           - 주제/대본은 내레이션으로 풀 수 있는 형식(기록 · 랭킹 · 물리 · 생존 이야기)으로
           - 화면은 액션 스톡 영상(POV · 슬로 모션 · 드론) 위주

trends.niche 로 고르고, 장르 값(search_queries, lookback_days …)은 config의 같은 키보다 우선한다.
직접 검색어를 정하고 싶으면 niche: general 로 두고 trends.search_queries 를 바꾸면 된다.
"""

from __future__ import annotations

import re

NICHES: dict[str, dict] = {
    "general": {
        "label": "일반 유행 (급상승 전체)",
    },
    "extreme": {
        "label": "익스트림 · 도파민 (극한 스포츠 · 아찔한 순간)",
        # 검색 1번 = 쿼터 100 → 6개면 600 (하루 무료 10,000). 결과는 캐시로 몇 시간 재사용.
        "search_queries": ["extreme sports", "insane stunt", "close call", "wingsuit", "big wave surfing",
                           "storm chasing"],
        "popular_category": "17",          # 인기 급상승은 스포츠 카테고리만
        "popular_pages": 2,
        "lookback_days": 7,                # 장르 쇼츠는 수가 적어서 1주일
        "min_views": 20000,
        "keywords": [
            "extreme", "insane", "crazy", "stunt", "adrenaline", "dangerous", "deadliest", "scariest",
            "wingsuit", "skydiv", "base jump", "parachute", "paraglid", "bungee", "free ?fall",
            "surf", "big wave", "tsunami", "cliff", "free solo", "climb", "mountain", "everest", "avalanche",
            "ski", "snowboard", "motocross", "dirt ?bike", "bmx", "skate", "parkour", "rooftop", "drift",
            "rally", "speed", "fastest", "highest", "deepest", "rescue", "surviv", "close call", "near miss",
            "almost", "storm", "tornado", "hurricane", "lightning", "volcano", "lava", "waterfall", "rapids",
            "kayak", "shark", "crocodile", "bear", "lion", "snake", "rodeo", "bull", "jump", "flip",
            "record", "world's", "pov",
        ],
        "focus_en": (
            "[Channel niche: EXTREME / ADRENALINE]\n"
            "This channel only makes dopamine-heavy Shorts: extreme sports, insane stunts, close calls, "
            "nature's raw power (storms, waves, avalanches, volcanoes), wild animal encounters, records and "
            "survival stories.\n"
            "- Every topic must belong to this niche, even if other trends appear in the data.\n"
            "- It is told as narration over action stock footage (no need for the original clips).\n"
            "- DEFAULT FORMAT = ONE heart-stopping moment told like a thriller, present tense, second by second:\n"
            "  1) Line 1 = the stakes + a ticking clock ('He has four seconds to open his parachute.')\n"
            "  2) Small concrete details that raise the stakes (wind, height, distance to the edge, what fails)\n"
            "  3) A complication — the plan goes wrong\n"
            "  4) Near the climax: very short lines (3-8 words), a countdown ('Three. Two...'), 'and then—'\n"
            "  5) Hold the answer for ONE more line, then the payoff or twist. Never reveal the ending early.\n"
            "  Sometimes put the viewer inside it ('Imagine you're 30,000 feet up...').\n"
            "- Use an anonymous protagonist ('a surfer', 'a climber') unless it is a famous, well-documented "
            "record; don't invent precise facts about real people. Rankings/lists only occasionally.\n"
            "- Never encourage viewers to try dangerous stunts, no gore, no real injuries or deaths described "
            "in graphic detail, no claims about specific private people."
        ),
        "focus_ko": (
            "[채널 장르: 익스트림 · 도파민]\n"
            "이 채널은 극한 스포츠, 아찔한 묘기, 간발의 차로 피한 순간, 대자연의 위력(폭풍 · 파도 · 눈사태 · 화산), "
            "야생동물과의 조우, 기록과 생존 이야기만 다룬다.\n"
            "- 데이터에 다른 유행이 있어도 주제는 반드시 이 장르 안에서 고른다.\n"
            "- 액션 스톡 영상 위 내레이션.\n"
            "- 기본 형식 = 심장 쫄리는 '한 순간'을 스릴러처럼, 현재형으로, 초 단위로:\n"
            "  1) 첫 줄 = 걸린 것 + 시간 압박 ('그에게 남은 시간은 4초.')\n"
            "  2) 위험을 키우는 구체적인 묘사 (바람, 높이, 낭떠러지까지 거리, 고장 난 장비)\n"
            "  3) 계획이 틀어지는 순간\n"
            "  4) 클라이맥스 직전엔 아주 짧은 문장, 카운트다운 ('셋. 둘...'), '그리고 그 순간—'\n"
            "  5) 답을 한 줄 더 미룬 뒤 결말이나 반전. 결말을 미리 말하지 말 것.\n"
            "- 주인공은 익명('한 서퍼', '한 등반가'). 실존 인물에 대한 정확한 사실을 지어내지 말 것.\n"
            "- 위험한 행동을 따라 하라고 하지 말 것, 잔인한 묘사 금지, 특정 일반인에 대한 주장 금지."
        ),
        # 현장감: 관중 리액션 컷 (스톡 영상 검색어) · 관중 함성(sfx/crowd 폴더) · AI 이미지 스타일
        "crowd_queries": ["crowd cheering", "stadium crowd", "spectators cheering", "crowd watching event",
                          "people filming with phones", "audience shocked"],
        "crowd_sfx": True,
        "suspense": True,                  # 긴장감 연출: 컷 가속 · 클라이맥스 슬로 모션 · 심장 박동 · 임팩트
        "image_style": ("live event sports photography, telephoto lens, crowd of spectators in the background, "
                        "real photo, motion blur, natural light, candid"),
        # 장면 검색어 · AI 이미지 지시 (영상 1단계 프롬프트에 붙는다)
        "visual_en": (
            "This is an extreme/adrenaline Short. Pick high-energy ACTION stock footage: POV helmet cam, "
            "slow motion, aerial drone shots, skydiving, wingsuit, big wave surfing, motocross jumps, "
            "snowboarding, rock climbing, storms, lightning, waves crashing. Avoid calm or office footage. "
            "AI image prompts: dramatic real-looking action moments at a live event with spectators watching, "
            "motion blur, low angle, golden hour."
        ),
    },
}

DEFAULT_NICHE = "extreme"
NICHE_KEYS = ("search_queries", "popular_category", "popular_pages", "lookback_days", "min_views", "keywords")


def niche_of(settings: dict | None) -> dict:
    name = (settings or {}).get("niche") or DEFAULT_NICHE
    return NICHES.get(name, NICHES["general"])


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
    return niche_of(settings)["label"]
