from __future__ import annotations

from dataclasses import dataclass

from resell_radar.models import FCFS, Release, Score
from resell_radar.tiers import NOTCHES, find_collaborators, find_hot_silhouette, is_apparel, notch

RECOMMEND = "추천"
MAYBE = "애매"
PASS = "패스"


@dataclass
class ProfitSettings:
    min_profit: int = 30_000  # KRW; below this a win isn't worth the effort
    min_profit_ratio: float = 0.20  # net profit / retail
    sell_fee_rate: float = 0.06  # resale platform fee — check the platform's current rate
    shipping_cost: int = 4_000


RECOMMEND_FROM = notch("A-")


def _shift(grade: str, delta: int) -> str:
    return NOTCHES[max(0, min(len(NOTCHES) - 1, notch(grade) + delta))]


def _fmt(delta: int) -> str:
    return f"{delta:+d}칸"


def score_release(
    release: Release,
    tiers: dict,
    profit: ProfitSettings,
    recent_collab_counts: dict[str, int] | None = None,
    estimator=None,
) -> Score:
    """Grade a release C-..S+ from scarcity signals, then gate it on expected profit.

    recent_collab_counts maps collaborator -> how many *other* releases with
    that partner were seen in the past year (from local history).
    estimator (learning.Estimator) replaces the fixed tier multipliers with
    ones corrected by recorded resale prices.
    """
    recent_collab_counts = recent_collab_counts or {}
    rules = tiers["rules"]
    adjust = tiers["adjust"]
    reasons: list[str] = []

    def apply(key: str, label: str) -> None:
        nonlocal grade
        grade = _shift(grade, adjust[key])
        reasons.append(f"{label} {_fmt(adjust[key])}")

    collabs = find_collaborators(release.name, tiers)
    if collabs:
        grade = max((g for _, g in collabs), key=notch)
        names = ", ".join(f"{n}({g})" for n, g in collabs)
        reasons.append(f"협업: {names}")
    else:
        grade = "C"
        reasons.append("알려진 협업 상대 없음")

    if len(collabs) >= 2:
        apply("multi_collab", "다자 협업")

    silhouette = find_hot_silhouette(release.name, tiers)
    if silhouette:
        apply("hot_silhouette", f"인기 실루엣({silhouette})")

    for name, _ in collabs:
        if recent_collab_counts.get(name, 0) >= rules["repeat_collab_count"]:
            apply("repeat_collab", f"{name} 최근 1년 협업 {recent_collab_counts[name]}회")
            break

    if release.is_restock:
        apply("restock", "재발매")

    if release.retailer_count is not None and release.retailer_count >= rules["many_retailers"]:
        apply("many_retailers", f"발매처 {release.retailer_count}곳")

    if release.method == FCFS:
        apply("fcfs", "선착순 판매")

    if release.is_apparel or is_apparel(release.name, tiers):
        apply("apparel", "의류")

    score = Score(level=grade, verdict=PASS, collaborators=[n for n, _ in collabs], reasons=reasons)

    if not release.retail_price:
        score.verdict = MAYBE if notch(grade) >= RECOMMEND_FROM else PASS
        reasons.append("정가 정보 없음 — 수익 계산 불가")
        return score

    if release.market_price:
        est_price = release.market_price
        score.price_is_estimate = False
        score.price_basis = "시세 기준"
    elif estimator is not None:
        est = estimator.estimate(release.name, grade)
        est_price = round(release.retail_price * est.multiplier)
        score.price_basis = est.basis
    else:
        est_price = round(release.retail_price * tiers["price_multiplier"][grade])
    net = round(est_price * (1 - profit.sell_fee_rate)) - profit.shipping_cost - release.retail_price
    score.est_price = est_price
    score.est_profit = net
    score.profit_ratio = net / release.retail_price

    if net < profit.min_profit or score.profit_ratio < profit.min_profit_ratio:
        reasons.append(f"예상 순수익 {net:,}원 — 기준 미달")
        score.verdict = PASS
    elif notch(grade) >= RECOMMEND_FROM:
        score.verdict = RECOMMEND
    else:
        score.verdict = MAYBE
    return score
