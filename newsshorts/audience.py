from __future__ import annotations

from dataclasses import dataclass, field

from newsshorts.sources import NewsItem


@dataclass(frozen=True)
class Audience:
    name: str
    # Extra instructions appended to the script-writing system prompt.
    prompt: str
    # Topic keywords → weight; new items are processed highest score first.
    topic_weights: dict[str, int] = field(default_factory=dict)
    voice_rate: str = "+10%"
    # Multiplier on on-screen font sizes.
    font_scale: float = 1.0
    # Max subtitle lines on screen at once.
    subtitle_lines: int = 3
    # Draw subtitles on a solid box instead of over the background.
    subtitle_box: bool = False
    default_hashtags: tuple[str, ...] = ("뉴스", "속보", "쇼츠")


GENERAL = Audience(name="general", prompt="")

SENIOR = Audience(
    name="senior",
    prompt="""
시청자: 50~70대 중장년·노년층. 이 시청자에 맞춰 쓴다.
- 말투: 뉴스 앵커처럼 차분하고 정중한 '~습니다' 체. 반말, 유행어, 영어 약어(MZ, 팩폭 등)는 쓰지 않는다.
  어려운 용어나 제도 이름이 나오면 바로 뒤에 한 번 쉽게 풀어 준다.
- 숫자는 구체적으로 쓴다. 금액은 "월 33만 원", 나이는 "65세 이상"처럼 기사에 있는 그대로 또렷하게.
- 기사에 근거가 있을 때만, 시청자 생활에 미치는 영향(연금·건강보험·세금·물가·의료비·집값 등)을
  "누가, 언제부터, 얼마나" 형태로 한 문장 넣는다. 근거가 없으면 영향을 추측해서 쓰지 않는다.
- 첫 문장에서 이 소식이 왜 시청자와 관련 있는지 바로 말한다.
- 제목은 이 연령대가 잘 누르는 형태로 쓴다:
  · 대상과 변화가 바로 보이게: "65세 이상 기초연금, 내년부터 이렇게 바뀝니다"
  · 잘 알려진 인물이나 기관 이름을 앞에: "OOO 대통령 '…' 발언"
  · 짧은 질문형도 가능: "건강보험료, 나도 오를까?"
  단, 제목은 기사 내용과 정확히 맞아야 하고 불안을 부풀리는 표현("큰일 났다", "당장 확인 안 하면 손해")은 쓰지 않는다.
- 해시태그에는 관련 제도·주제 이름(예: 기초연금, 건강보험)을 넣는다.""",
    topic_weights={
        # 돈·생활
        "연금": 5, "기초연금": 6, "국민연금": 6, "건강보험": 5, "보험료": 4, "의료비": 4,
        "세금": 4, "종부세": 4, "재산세": 4, "상속": 4, "증여": 3, "물가": 3, "금리": 3,
        "부동산": 3, "집값": 3, "전세": 2, "지원금": 4, "복지": 3, "노인": 4, "어르신": 4,
        "고령": 3, "은퇴": 3, "일자리": 2, "요양": 4, "간병": 4, "치매": 4,
        # 건강
        "건강": 3, "병원": 2, "독감": 2, "백신": 2,
        # 안전·사기
        "보이스피싱": 5, "사기": 3,
        # 정치 큰 이슈
        "대통령": 3, "국회": 2, "선거": 3, "총리": 2, "개헌": 2,
        # 미국 소식 중 한국 생활과 맞닿은 것
        "트럼프": 3, "관세": 4, "환율": 4, "주한미군": 5, "방위비": 4, "연준": 3, "북한": 3,
        "trump": 3, "tariff": 4, "korea": 5, "federal reserve": 3, "interest rate": 3,
        "social security": 3, "medicare": 3,
    },
    voice_rate="+0%",
    font_scale=1.15,
    subtitle_lines=2,
    subtitle_box=True,
    default_hashtags=("뉴스", "시사", "생활정보"),
)

PRESETS = {a.name: a for a in (GENERAL, SENIOR)}


def get_audience(name: str) -> Audience:
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(f"unknown audience {name!r}; choose from {sorted(PRESETS)}") from None


def topic_score(item: NewsItem, audience: Audience) -> int:
    title = item.title.lower()
    text = f"{title} {item.summary.lower()}"
    # Title hits count double: the title is what the viewer sees first.
    return sum(w * (2 * (k in title) + (k in text)) for k, w in audience.topic_weights.items())


def rank_items(items: list[NewsItem], audience: Audience) -> list[NewsItem]:
    """Most relevant to the audience first; stable for ties so feed order is kept."""
    return sorted(items, key=lambda i: topic_score(i, audience), reverse=True)
