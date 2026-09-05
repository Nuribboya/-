"""화면 표시용 0~100 종합 점수.

애널리스트 의견이 아니라 이미 계산된 두 절댓값 신호 — 안전마진(내재가치 대비
할인율)과 그레이엄 7원칙 충족 비율 — 만 가중합해 만든 보조 지표다. 실제
저평가 판정 기준은 여전히 `ValuationResult.is_undervalued`이며, 이 점수는
여러 종목을 한 화면에서 비교하기 쉽게 정렬/표시하기 위한 용도일 뿐이다.
"""
from __future__ import annotations

from typing import Optional

from stockscreener.models import GrahamAnalysis, ValuationResult

MOS_WEIGHT = 0.6
GRAHAM_WEIGHT = 0.4
MOS_RANGE_PCT = 50.0  # 안전마진 ±50%를 점수 0~100 범위에 선형 매핑한다


def _mos_score(margin_of_safety: Optional[float]) -> float:
    if margin_of_safety is None:
        return 50.0
    pct = max(-MOS_RANGE_PCT, min(MOS_RANGE_PCT, margin_of_safety * 100))
    return (pct + MOS_RANGE_PCT) / (2 * MOS_RANGE_PCT) * 100


def _graham_score(graham: Optional[GrahamAnalysis]) -> float:
    if graham is None or graham.evaluable_count == 0:
        return 50.0
    return graham.passed_count / graham.evaluable_count * 100


def composite_score(
    valuation: Optional[ValuationResult], graham: Optional[GrahamAnalysis]
) -> float:
    mos = _mos_score(valuation.margin_of_safety if valuation else None)
    gs = _graham_score(graham)
    score = MOS_WEIGHT * mos + GRAHAM_WEIGHT * gs
    return round(max(0.0, min(100.0, score)), 1)


def score_category(score: float) -> str:
    if score >= 65:
        return "저평가 후보"
    if score >= 40:
        return "적정~관망"
    return "고평가/주의"
