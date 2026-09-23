"""후보 평가 — 겹침 판정 → 업종 매칭 → 적합도 계산 순으로 채운다.

점수 계산 자체는 fitness.py 에 있다. 여기서는 '어떤 후보를 아예 뺄 것인가'
(기존 원청과 겹침, 거리 초과, 이력 부족)를 다룬다.

겹치는 후보는 점수를 깎는 게 아니라 목록에서 뺀다. '겹치지 않는 곳'이
요구사항인데 감점으로 섞어 두면 그 조건이 순위에 묻힌다.
"""
from __future__ import annotations

from prime_contractor.config import ScreenConfig
from prime_contractor.fitness import GRADE_CUTS, evaluate
from prime_contractor.industry import judge_overlap, match_sector
from prime_contractor.models import Candidate


def score_candidate(cand: Candidate, cfg: ScreenConfig) -> Candidate:
    """후보 하나를 평가해 overlap / sector / fitness 를 채운다."""
    cand.overlap = judge_overlap(cand, cfg.incumbent)
    cand.sector, cand.sector_weight, _why = match_sector(cand, cfg.sectors)

    fit = evaluate(cand, max_distance_km=cfg.max_distance_km, weights=cfg.weights,
                   our_monthly_revenue=cfg.our_monthly_revenue,
                   lookback_days=cfg.lookback_days)
    cand.fitness = fit
    cand.score = fit.total
    cand.grade = fit.grade
    return cand


def grade_at_least(grade: str, minimum: str) -> bool:
    """A/B/C/D 비교. 'B' 이상이면 A 와 B 가 통과."""
    order = [g for _, g in GRADE_CUTS]          # ['A', 'B', 'C', 'D']
    try:
        return order.index(grade) <= order.index(minimum)
    except ValueError:
        return True                              # 알 수 없는 등급이면 거르지 않는다


def split_by_overlap(cands: list[Candidate], cfg: ScreenConfig) -> tuple[list[Candidate], list[Candidate]]:
    """(통과, 제외) 로 나눈다. 제외 사유는 각 후보의 overlap 에 들어 있다.

    거리는 '확인된 경우에만' 자른다. 주소를 못 찾은 후보까지 묶어서 버리면
    멀다는 근거도 없이 사라지므로, 남겨 두고 표에 '미상'으로 보여 준다.
    """
    passed, excluded = [], []
    for c in cands:
        assert c.overlap is not None, "score_candidate 를 먼저 호출해야 합니다"
        too_far = (cfg.within_km is not None
                   and c.distance_km is not None
                   and c.distance_km > cfg.within_km)

        if c.business_closed and cfg.drop_closed_businesses:
            # 폐업한 곳은 점수가 아무리 높아도 연락할 이유가 없다.
            c.overlap.reasons.append(c.status_note or "국세청 확인: 폐업")
            excluded.append(c)
        elif c.overlap.rank > cfg.max_overlap_rank:
            excluded.append(c)
        elif too_far:
            c.overlap.reasons.append(
                f"안성에서 {c.distance_km:.0f}km (기준 {cfg.within_km:.0f}km 초과)")
            excluded.append(c)
        elif c.award_count < cfg.min_awards and c.kind == "contractor":
            c.overlap.reasons.append(f"낙찰 이력 {c.award_count}건 (최소 {cfg.min_awards}건 미달)")
            excluded.append(c)
        elif cfg.min_grade and not grade_at_least(c.grade, cfg.min_grade):
            c.overlap.reasons.append(f"{c.grade}등급 (기준 {cfg.min_grade}등급 미만)")
            excluded.append(c)
        else:
            passed.append(c)

    passed.sort(key=lambda c: c.score, reverse=True)
    excluded.sort(key=lambda c: c.score, reverse=True)
    return passed, excluded
