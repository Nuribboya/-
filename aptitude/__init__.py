"""전공 적성검사(홀랜드 RIASEC 기반) 패키지.

`score_answers` 하나만 알면 됩니다: 문항ID -> 응답값(1~5) 딕셔너리를 넣으면
유형별 점수, 홀랜드 코드, 추천 전공 목록을 담은 `AptitudeResult` 를 돌려줍니다.
"""
from .majors import MAJORS, Major
from .questions import LIKERT_LABELS, QUESTIONS, Question
from .scorer import AptitudeResult, MajorRecommendation, score_answers
from .traits import TRAIT_ORDER, TRAITS, Trait

__all__ = [
    "MAJORS",
    "Major",
    "LIKERT_LABELS",
    "QUESTIONS",
    "Question",
    "AptitudeResult",
    "MajorRecommendation",
    "score_answers",
    "TRAIT_ORDER",
    "TRAITS",
    "Trait",
]
