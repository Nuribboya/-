import json
from types import SimpleNamespace

from leadradar.config import CompanyProfile
from leadradar.models import Candidate
from leadradar.scoring import _extract_json, score_candidate


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeMessages:
    def __init__(self, response_json: dict):
        self._response_json = response_json
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(content=[_FakeTextBlock(json.dumps(self._response_json))])


class _FakeClient:
    def __init__(self, response_json: dict):
        self.messages = _FakeMessages(response_json)


def test_score_candidate_parses_response():
    own = CompanyProfile(name="우리회사", business_description="반도체 장비용 제어반 제작")
    excluded = CompanyProfile(name="케이씨그룹", business_description="반도체 장비 유통")
    candidate = Candidate(name="테스트회사", business_description="반도체 검사장비 제조", distance_km=20, revenue_growth_pct=15.0)

    fake_client = _FakeClient(
        {"fit_score": 87, "conflicts_with_excluded_client": False, "reasoning": "사업영역이 겹치지 않고 성장성이 좋음"}
    )

    result = score_candidate(fake_client, own, excluded, candidate)

    assert result.fit_score == 87
    assert result.conflicts_with_excluded_client is False
    assert "겹치지" in result.reasoning
    assert result.candidate is candidate


def test_extract_json_handles_markdown_code_fence():
    raw = '```json\n{"fit_score": 55, "conflicts_with_excluded_client": false, "reasoning": "무난함"}\n```'

    parsed = _extract_json(raw)

    assert parsed == {"fit_score": 55, "conflicts_with_excluded_client": False, "reasoning": "무난함"}


def test_extract_json_handles_leading_explanation_text():
    raw = '알겠습니다. 아래가 평가 결과입니다:\n{"fit_score": 70, "conflicts_with_excluded_client": true, "reasoning": "겹침"}'

    parsed = _extract_json(raw)

    assert parsed["fit_score"] == 70
    assert parsed["conflicts_with_excluded_client"] is True


def test_score_candidate_flags_conflict():
    own = CompanyProfile(name="우리회사", business_description="반도체 장비용 제어반 제작")
    excluded = CompanyProfile(name="케이씨그룹", business_description="반도체 제어반 유통")
    candidate = Candidate(name="경쟁회사", business_description="반도체 제어반 유통")

    fake_client = _FakeClient(
        {"fit_score": 20, "conflicts_with_excluded_client": True, "reasoning": "케이씨그룹과 사업영역이 겹침"}
    )

    result = score_candidate(fake_client, own, excluded, candidate)

    assert result.conflicts_with_excluded_client is True
