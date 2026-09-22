"""후보 점수화.

4개 축으로 100점을 나눈다.
  sector     - 판넬 수요가 큰 업종인가
  proximity  - 안성에서 가까운가 (운송비·현장 대응)
  activity   - 최근 발주/수주가 실제로 있는가
  profile    - 판정 근거가 얼마나 확실한가 (업종코드·사업자번호 확보 여부)

업종이 겹치는 후보는 점수를 깎는 게 아니라 아예 필터에서 제외한다.
'겹치지 않는 곳'이 요구사항이라 감점으로 섞어 두면 순위가 오염된다.
"""
from __future__ import annotations

import math

from prime_contractor.config import ScreenConfig
from prime_contractor.industry import MAX_SECTOR_STRENGTH, judge_overlap, match_sector
from prime_contractor.models import Candidate


def _proximity_points(distance_km: float | None, max_km: float, full: float) -> float:
    if distance_km is None:
        return full * 0.3          # 주소 미상은 중간보다 낮게 (확인 필요 신호)
    if distance_km >= max_km:
        return 0.0
    return full * (1 - distance_km / max_km)


def _activity_points(cand: Candidate, full: float) -> float:
    """건수와 금액을 로그로 눌러 대형 1건이 전부를 먹지 않게 한다."""
    if cand.award_count == 0:
        return 0.0
    count_part = min(math.log1p(cand.award_count) / math.log(6), 1.0)      # 5건이면 만점
    amount_eok = cand.award_amount / 1e8
    amount_part = min(math.log1p(amount_eok) / math.log(51), 1.0)          # 50억이면 만점
    return full * (0.5 * count_part + 0.5 * amount_part)


def _profile_points(cand: Candidate, full: float) -> float:
    filled = sum(bool(v) for v in (cand.ksic_code, cand.bizno, cand.address, cand.ceo))
    return full * filled / 4


def score_candidate(cand: Candidate, cfg: ScreenConfig) -> Candidate:
    """후보 하나를 평가해 overlap/sector/score 를 채운다."""
    cand.overlap = judge_overlap(cand, cfg.incumbent)
    sector, weight, _why = match_sector(cand, cfg.sectors)
    cand.sector = sector
    cand.sector_weight = weight

    w = cfg.weights
    breakdown = {
        "sector": round(min(weight, MAX_SECTOR_STRENGTH) / MAX_SECTOR_STRENGTH * w["sector"], 1),
        "proximity": round(_proximity_points(cand.distance_km, cfg.max_distance_km, w["proximity"]), 1),
        "activity": round(_activity_points(cand, w["activity"]), 1),
        "profile": round(_profile_points(cand, w["profile"]), 1),
    }
    cand.score_breakdown = breakdown
    cand.score = round(sum(breakdown.values()), 1)
    return cand


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
        if c.overlap.rank > cfg.max_overlap_rank:
            excluded.append(c)
        elif too_far:
            c.overlap.reasons.append(
                f"안성에서 {c.distance_km:.0f}km (기준 {cfg.within_km:.0f}km 초과)")
            excluded.append(c)
        elif c.award_count < cfg.min_awards and c.kind == "contractor":
            c.overlap.reasons.append(f"낙찰 이력 {c.award_count}건 (최소 {cfg.min_awards}건 미달)")
            excluded.append(c)
        else:
            passed.append(c)
    passed.sort(key=lambda c: c.score, reverse=True)
    excluded.sort(key=lambda c: c.score, reverse=True)
    return passed, excluded
