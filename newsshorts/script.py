from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from newsshorts.audience import GENERAL, Audience
from newsshorts.log import log
from newsshorts.sources import NewsItem

SYSTEM_PROMPT = """너는 한국어 뉴스 쇼츠(30~50초) 대본 작가다. 주어진 기사 정보만으로 대본을 쓴다.

원칙:
- 기사에 없는 사실, 수치, 발언을 절대 만들지 않는다. 모르면 쓰지 않는다.
- 발언은 "OO에 따르면", "OO는 '...'라고 밝혔다"처럼 출처를 붙인다.
- 확정된 사실과 보도·주장·전망을 구분한다. 원문이 "검토 중"이면 "확정"이라고 쓰지 않는다.
- 제목은 강하게 쓰되 사실이어야 한다. 원문에서 가장 센 사실이나 발언을 앞에 둔다.
  "충격", "결국", "난리" 같은 내용 없는 낚시 표현과 특정 진영 비하 표현은 쓰지 않는다.
- 문장은 짧게, 말로 읽기 좋게 쓴다. 한 문장은 40자 안팎.
- 정치적으로 어느 편도 들지 않고 사실을 전달한다.
- 기사 유형이 '전망'이면 원문보다 더 확신하지 않는다. "~할 가능성", "~를 검토 중",
  "OO는 ~라고 전망했다"처럼 원문 수준의 확신으로만 쓰고, 누가 그렇게 보는지 반드시 밝힌다.
- 출처 없는 소문, 정치인의 사생활·건강·가족에 관한 의혹은 기사에 있어도 옮기지 않는다.
- 제목에 [속보], [단독] 같은 대괄호 말머리를 넣지 않는다. 말머리는 프로그램이 원문 기준으로 붙인다.
- 영어 기사면 한국어로 옮긴다. 인용 발언은 "~라고 말했습니다"처럼 자연스러운 한국어로 옮기고,
  인물·기관 이름은 한국 언론 표기(트럼프 대통령, 연방준비제도 등)를 쓴다.
  기사에 근거가 있을 때만 한국에 미치는 영향(환율, 관세, 수출, 주한미군 등)을 한 문장 덧붙인다."""

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "유튜브 제목, 60자 이내"},
        "quote": {"type": "string", "description": "화면에 크게 띄울 핵심 문장. 원문 표현 그대로"},
        "sentences": {
            "type": "array",
            "items": {"type": "string"},
            "description": "읽을 대본 문장들, 5~8개",
        },
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string", "description": "영상 설명란 2~3줄"},
    },
    "required": ["title", "quote", "sentences", "hashtags", "description"],
    "additionalProperties": False,
}


@dataclass
class Script:
    title: str
    quote: str
    sentences: list[str]
    hashtags: list[str] = field(default_factory=list)
    description: str = ""
    # "claude" or "template"; only Claude scripts are eligible for auto-publish.
    generator: str = "claude"

    def to_dict(self) -> dict:
        return asdict(self)


def _user_prompt(item: NewsItem) -> str:
    from newsshorts.labels import report_type

    return (
        f"출처: {item.source} ({'미국' if item.region == 'us' else '한국'} 언론)\n"
        f"기사 유형: {report_type(item)}\n"
        f"제목: {item.title}\n"
        f"요약/본문: {item.summary or '(없음)'}\n"
        f"링크: {item.link}\n\n"
        "이 기사로 쇼츠 대본을 써줘. 첫 문장은 시청자를 붙잡는 핵심 한 줄로 시작해."
    )


def generate_with_claude(item: NewsItem, model: str, audience: Audience = GENERAL) -> Script:
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT + audience.prompt,
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": SCRIPT_SCHEMA},
        },
        messages=[{"role": "user", "content": _user_prompt(item)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined this item: {response.stop_details}")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    return Script(**data)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p and p.strip()]
    return parts


def generate_offline(item: NewsItem, audience: Audience = GENERAL) -> Script:
    """Template script used when no Claude credentials are configured.

    It only rearranges the feed's own title and summary, so it never adds facts —
    but it reads flatter than the Claude version.
    """
    sentences = [f"{item.source} 보도입니다.", item.title]
    sentences += split_sentences(item.summary)[:5]
    sentences.append("자세한 내용은 설명란 원문 링크에서 확인하세요.")
    return Script(
        title=item.title[:60],
        quote=item.title,
        sentences=sentences,
        hashtags=list(audience.default_hashtags),
        description=f"출처: {item.source}\n{item.link}",
        generator="template",
    )


FACT_CHECK_PROMPT = """너는 뉴스 영상 검수자다. 원문 기사와 영상 대본을 비교해 문제를 찾는다.
문제로 보는 것:
- 원문에 없는 사실, 수치, 날짜, 인물, 발언
- 원문보다 확신을 키운 표현 (검토 중 → 확정, 가능성 → 사실)
- 이름, 직함, 숫자, 단위의 오류나 잘못된 번역
- 출처 없는 소문, 사생활·건강·가족 의혹
- 특정 집단·진영을 비하하거나 적대감을 부추기는 표현
원문을 한국어로 자연스럽게 옮기거나 요약한 것은 문제가 아니다.
문제가 하나라도 있으면 ok는 false다."""

FACT_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ok", "problems"],
    "additionalProperties": False,
}


def fact_check(item: NewsItem, script: Script, model: str) -> dict:
    """Second, independent Claude pass that compares the script with the source."""
    import anthropic

    client = anthropic.Anthropic()
    script_text = "\n".join([f"제목: {script.title}", f"인용: {script.quote}", *script.sentences])
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=FACT_CHECK_PROMPT,
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": FACT_CHECK_SCHEMA},
        },
        messages=[{"role": "user", "content": (
            f"[원문]\n출처: {item.source}\n제목: {item.title}\n본문: {item.summary}\n\n"
            f"[대본]\n{script_text}"
        )}],
    )
    if response.stop_reason == "refusal":
        return {"ok": False, "problems": ["검수 모델이 응답을 거절함"]}
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def make_script(item: NewsItem, model: str, use_claude: bool = True, audience: Audience = GENERAL) -> Script:
    if use_claude:
        try:
            return generate_with_claude(item, model, audience)
        except Exception as exc:
            log(f"[warn] Claude script failed, using offline template: {exc}")
    return generate_offline(item, audience)
