"""LLM(Claude)으로 후보 회사의 신규 원청 적합도를 실시간으로 판단한다.

새 모델을 학습시키는 게 아니라, 이미 학습된 Claude에게 후보 데이터를 매번 넣어서
그 자리에서 추론시키는 방식이라 후보가 추가될 때마다 바로 점수를 매길 수 있다.
"""
from __future__ import annotations

import json
import os
import re

import anthropic

from .config import CompanyProfile
from .models import Candidate, ScoredCandidate

_SYSTEM_PROMPT = """\
당신은 소규모 제조업체의 신규 원청(고객사) 발굴을 돕는 분석가입니다.
주어진 후보 회사가 우리 회사의 신규 원청으로 얼마나 적합한지 평가하세요.

반드시 아래 JSON 형식으로만 답하고 다른 텍스트는 출력하지 마세요:
{"fit_score": 0부터 100 사이 정수, "conflicts_with_excluded_client": true 또는 false, "reasoning": "한두 문장 이유"}

conflicts_with_excluded_client는 후보 회사의 사업영역이 기존 원청(관계사)과 겹쳐서
영업하면 그 관계에 문제가 생길 수 있는 경우에만 true로 표시하세요.

거리는 차로 40~50분 이내(대략 40~50km 이내)면 실사/납품에 무리가 없다고 보고
감점하지 마세요. 그보다 멀면 거리에 비례해 감점하세요.
"""


def _build_user_prompt(
    own_company: CompanyProfile,
    excluded_client: CompanyProfile,
    candidate: Candidate,
) -> str:
    distance = "정보없음" if candidate.distance_km is None else f"{candidate.distance_km}km"
    growth = "정보없음" if candidate.revenue_growth_pct is None else f"{candidate.revenue_growth_pct}%"
    return f"""\
[우리 회사]
이름: {own_company.name}
사업분야: {own_company.business_description}

[기존 원청 - 사업영역이 겹치면 관계에 문제가 되는 회사]
이름: {excluded_client.name}
사업분야: {excluded_client.business_description}

[평가할 후보 회사]
이름: {candidate.name}
사업분야: {candidate.business_description}
거리: {distance}
최근 매출성장률: {growth}
출처: {candidate.source}
"""


def build_client(api_key: str | None = None) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])


def _extract_json(raw_text: str) -> dict:
    """모델 응답에서 JSON 객체를 뽑아낸다.

    시스템 프롬프트로 "JSON만 출력"을 지시해도 코드블럭(```json ... ```)이나
    앞뒤에 짧은 설명을 붙이는 경우가 있어, 첫 '{'부터 마지막 '}'까지만 잘라서
    파싱한다.
    """
    text = raw_text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"응답에서 JSON을 찾지 못했습니다: {raw_text!r}")
    return json.loads(match.group(0))


def score_candidate(
    client: anthropic.Anthropic,
    own_company: CompanyProfile,
    excluded_client: CompanyProfile,
    candidate: Candidate,
    model: str = "claude-sonnet-5",
) -> ScoredCandidate:
    message = client.messages.create(
        model=model,
        max_tokens=300,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": _build_user_prompt(own_company, excluded_client, candidate),
            }
        ],
    )
    raw_text = "".join(block.text for block in message.content if block.type == "text")
    parsed = _extract_json(raw_text)
    return ScoredCandidate(
        candidate=candidate,
        fit_score=int(parsed["fit_score"]),
        conflicts_with_excluded_client=bool(parsed["conflicts_with_excluded_client"]),
        reasoning=str(parsed["reasoning"]),
    )
