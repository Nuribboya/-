"""응답(문항ID -> 1~5점)을 받아 유형별 점수, 홀랜드 코드, 추천 전공을 계산."""
from dataclasses import dataclass
from typing import Dict, List

from .majors import MAJORS
from .questions import QUESTIONS
from .traits import TRAIT_ORDER

LIKERT_MIN = 1
LIKERT_MAX = 5

# 전공 추천 시 1순위(가장 가까운 유형)와 2순위 유형에 부여하는 가중치.
_PRIMARY_WEIGHT = 0.65
_SECONDARY_WEIGHT = 0.35


@dataclass
class MajorRecommendation:
    name: str
    holland_code: str
    description: str
    careers: List[str]
    match_score: float  # 0~100, 응답자의 유형 점수와 전공의 홀랜드 코드가 얼마나 맞는지


@dataclass
class AptitudeResult:
    trait_scores: Dict[str, float]  # 유형 코드 -> 0~100 점수
    holland_code: str  # 점수가 가장 높은 두 유형을 이어붙인 코드, 예: "IR"
    top_recommendations: List[MajorRecommendation]


def _validate_answers(answers: Dict[str, int]) -> None:
    missing = [q.id for q in QUESTIONS if q.id not in answers]
    if missing:
        raise ValueError(f"응답이 누락된 문항이 있습니다: {', '.join(missing)}")
    for q in QUESTIONS:
        value = answers[q.id]
        if not isinstance(value, int) or isinstance(value, bool) or not (LIKERT_MIN <= value <= LIKERT_MAX):
            raise ValueError(f"'{q.id}' 응답 값은 {LIKERT_MIN}~{LIKERT_MAX} 사이의 정수여야 합니다.")


def score_answers(answers: Dict[str, int], top_n: int = 5) -> AptitudeResult:
    """문항ID -> 응답값(1~5) 딕셔너리를 받아 적성검사 결과를 계산한다."""
    _validate_answers(answers)

    raw_sums: Dict[str, int] = {code: 0 for code in TRAIT_ORDER}
    counts: Dict[str, int] = {code: 0 for code in TRAIT_ORDER}
    for q in QUESTIONS:
        raw_sums[q.trait] += answers[q.id]
        counts[q.trait] += 1

    trait_scores: Dict[str, float] = {}
    for code in TRAIT_ORDER:
        n = counts[code]
        lo, hi = LIKERT_MIN * n, LIKERT_MAX * n
        trait_scores[code] = round((raw_sums[code] - lo) / (hi - lo) * 100, 1)

    ranked_traits = sorted(TRAIT_ORDER, key=lambda c: trait_scores[c], reverse=True)
    holland_code = "".join(ranked_traits[:2])

    recommendations = []
    for major in MAJORS:
        primary_score = trait_scores[major.primary]
        secondary_score = trait_scores[major.secondary] if major.secondary else primary_score
        match = primary_score * _PRIMARY_WEIGHT + secondary_score * _SECONDARY_WEIGHT
        recommendations.append(
            MajorRecommendation(
                name=major.name,
                holland_code=major.primary + (major.secondary or ""),
                description=major.description,
                careers=list(major.careers),
                match_score=round(match, 1),
            )
        )
    recommendations.sort(key=lambda r: (r.match_score, r.name), reverse=True)

    return AptitudeResult(
        trait_scores=trait_scores,
        holland_code=holland_code,
        top_recommendations=recommendations[:top_n],
    )
