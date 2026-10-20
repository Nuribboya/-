from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from newsshorts.sources import NewsItem

SYSTEM_PROMPT = """너는 한국어 뉴스 쇼츠(30~50초) 대본 작가다. 주어진 기사 정보만으로 대본을 쓴다.

원칙:
- 기사에 없는 사실, 수치, 발언을 절대 만들지 않는다. 모르면 쓰지 않는다.
- 발언은 "OO에 따르면", "OO는 '...'라고 밝혔다"처럼 출처를 붙인다.
- 확정된 사실과 보도·주장·전망을 구분한다. 원문이 "검토 중"이면 "확정"이라고 쓰지 않는다.
- 제목은 강하게 쓰되 사실이어야 한다. 원문에서 가장 센 사실이나 발언을 앞에 둔다.
  "충격", "결국", "난리" 같은 내용 없는 낚시 표현과 특정 진영 비하 표현은 쓰지 않는다.
- 문장은 짧게, 말로 읽기 좋게 쓴다. 한 문장은 40자 안팎.
- 정치적으로 어느 편도 들지 않고 사실을 전달한다."""

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

    def to_dict(self) -> dict:
        return asdict(self)


def _user_prompt(item: NewsItem) -> str:
    return (
        f"출처: {item.source}\n"
        f"제목: {item.title}\n"
        f"요약/본문: {item.summary or '(없음)'}\n"
        f"링크: {item.link}\n\n"
        "이 기사로 쇼츠 대본을 써줘. 첫 문장은 시청자를 붙잡는 핵심 한 줄로 시작해."
    )


def generate_with_claude(item: NewsItem, model: str) -> Script:
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
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


def generate_offline(item: NewsItem) -> Script:
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
        hashtags=["뉴스", "속보", "쇼츠"],
        description=f"출처: {item.source}\n{item.link}",
    )


def make_script(item: NewsItem, model: str, use_claude: bool = True) -> Script:
    if use_claude:
        try:
            return generate_with_claude(item, model)
        except Exception as exc:
            print(f"[warn] Claude script failed, using offline template: {exc}")
    return generate_offline(item)
