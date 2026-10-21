from __future__ import annotations

from dataclasses import dataclass

from resell_radar.models import FCFS, Release, Score
from resell_radar.tiers import GRADES, find_collaborators, find_hot_silhouette

RECOMMEND = "추천"
MAYBE = "애매"
PASS = "패스"


@dataclass
class ProfitSettings:
    min_profit: int = 30_000  # KRW; below this a win isn't worth the effort
    min_profit_ratio: float = 0.20  # net profit / retail
    sell_fee_rate: float = 0.06  # resale platform fee — check the platform's current rate
    shipping_cost: int = 4_000


def _shift(grade: str, delta: int) -> str:
    i = max(0, min(len(GRADES) - 1, GRADES.index(grade) + delta))
    return GRADES[i]


def score_release(
    release: Release,
    tiers: dict,
    profit: ProfitSettings,
    recent_collab_counts: dict[str, int] | None = None,
) -> Score:
    """Grade a release S..C from scarcity signals, then gate it on expected profit.

    recent_collab_counts maps collaborator -> how many *other* releases with
    that partner were seen in the past year (from local history).
    """
    recent_collab_counts = recent_collab_counts or {}
    rules = tiers["rules"]
    reasons: list[str] = []

    collabs = find_collaborators(release.name, tiers)
    if collabs:
        grade = max((g for _, g in collabs), key=GRADES.index)
        names = ", ".join(f"{n}({g})" for n, g in collabs)
        reasons.append(f"협업: {names}")
    else:
        grade = "C"
        reasons.append("알려진 협업 상대 없음")

    if len(collabs) >= 2:
        grade = _shift(grade, +1)
        reasons.append("다자 협업 +1")

    silhouette = find_hot_silhouette(release.name, tiers)
    if silhouette:
        grade = _shift(grade, +1)
        reasons.append(f"인기 실루엣({silhouette}) +1")

    for name, _ in collabs:
        if recent_collab_counts.get(name, 0) >= rules["repeat_collab_count"]:
            grade = _shift(grade, -1)
            reasons.append(f"{name} 최근 1년 협업 {recent_collab_counts[name]}회 -1")
            break

    if release.is_restock:
        grade = _shift(grade, -1)
        reasons.append("재발매 -1")

    if release.retailer_count is not None and release.retailer_count >= rules["many_retailers"]:
        grade = _shift(grade, -1)
        reasons.append(f"발매처 {release.retailer_count}곳 -1")

    if release.method == FCFS:
        grade = _shift(grade, -1)
        reasons.append("선착순 판매 -1")

    score = Score(level=grade, verdict=PASS, collaborators=[n for n, _ in collabs], reasons=reasons)

    if not release.retail_price:
        score.verdict = MAYBE if grade in ("S", "A") else PASS
        reasons.append("정가 정보 없음 — 수익 계산 불가")
        return score

    if release.market_price:
        est_price = release.market_price
        score.price_is_estimate = False
    else:
        est_price = round(release.retail_price * tiers["price_multiplier"][grade])
    net = round(est_price * (1 - profit.sell_fee_rate)) - profit.shipping_cost - release.retail_price
    score.est_price = est_price
    score.est_profit = net
    score.profit_ratio = net / release.retail_price

    if net < profit.min_profit or score.profit_ratio < profit.min_profit_ratio:
        reasons.append(f"예상 순수익 {net:,}원 — 기준 미달")
        score.verdict = PASS
    elif grade in ("S", "A"):
        score.verdict = RECOMMEND
    else:
        score.verdict = MAYBE
    return score
