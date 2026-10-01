"""기준 기간 평균을 잡아 두고, 그 뒤 달마다 기준과 견준다.

1~9월처럼 지난 몇 달을 '평소'로 보고, 10월부터 새 달 매출이 장부에 들어올
때마다 '평소보다 몇 % 많고 적은지 · 손익분기와 목표(목표이익 포함)는 넘겼는지'를
한 줄로 판정한다. 달마다 매출 하나만 더 적으면 되도록, 비용은 '월 고정비'와
'재료·외주비 %'로 잡은 손익분기 어림값(CostModel)을 그대로 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from prime_contractor.breakeven import CostModel
from prime_contractor.sales import SalesBook

#: 기준 평균보다 이만큼 넘게 줄면 '일감 확인'을 붙인다.
DROP_WARNING = 0.15


@dataclass
class Baseline:
    first: str                    # "2026-01"
    last: str                     # "2026-09"
    months: int
    avg_revenue: int
    avg_profit: int | None        # 비용 숫자가 없으면 None
    low: tuple[str, int]          # 가장 낮았던 달
    high: tuple[str, int]


@dataclass
class MonthCheck:
    ym: str
    revenue: int
    vs_baseline: float            # (매출 − 기준 평균) ÷ 기준 평균
    profit: int | None
    level: str                    # "good" | "warn" | "bad" | "open"(진행 중)
    verdict: str


@dataclass
class Review:
    start: str
    baseline: Baseline | None = None
    checks: list[MonthCheck] = field(default_factory=list)

    @property
    def closed(self) -> list[MonthCheck]:
        return [c for c in self.checks if c.level != "open"]


def review(book: SalesBook, cost: CostModel | None, start: str,
           today: date | None = None) -> Review:
    """start(예: "2026-10") 이전의 끝난 달로 기준을 잡고, start 부터의 달을 판정한다."""
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    result = Review(start=start)

    before = [m for m in book.sorted_months() if m.ym < start and m.ym < current and m.revenue]
    if before:
        revenues = [m.revenue for m in before]
        avg = int(sum(revenues) / len(revenues))
        low = min(before, key=lambda m: m.revenue)
        high = max(before, key=lambda m: m.revenue)
        result.baseline = Baseline(
            first=before[0].ym, last=before[-1].ym, months=len(before), avg_revenue=avg,
            avg_profit=(int(sum(cost.profit_at(r) for r in revenues) / len(revenues))
                        if cost else None),
            low=(low.ym, low.revenue), high=(high.ym, high.revenue))

    for m in book.sorted_months():
        if m.ym < start or not m.revenue:
            continue
        result.checks.append(_check(m.ym, m.revenue, result.baseline, cost, m.ym >= current))
    return result


def _check(ym: str, revenue: int, base: Baseline | None, cost: CostModel | None,
           in_progress: bool) -> MonthCheck:
    vs = (revenue - base.avg_revenue) / base.avg_revenue if base and base.avg_revenue else 0.0
    profit = cost.profit_at(revenue) if cost else None
    vs_text = f"평소보다 {abs(vs) * 100:.0f}% {'많음' if vs >= 0 else '적음'}" if base else ""

    if in_progress:
        need = cost.target - revenue if cost else 0
        text = f"진행 중 — 지금까지 {revenue / 1e4:,.0f}만원"
        if need > 0:
            text += f", 목표까지 {need / 1e4:,.0f}만원 남음"
        elif cost:
            text += ", 벌써 목표 넘김"
        return MonthCheck(ym, revenue, vs, None, "open", text)

    if cost is None:
        level = "warn" if vs <= -DROP_WARNING else "good"
        return MonthCheck(ym, revenue, vs, None, level,
                          vs_text + " (비용 숫자를 넣으면 손익도 판정합니다)")

    if profit < 0:
        level = "bad"
        text = f"적자 — 손익분기까지 {(cost.breakeven - revenue) / 1e4:,.0f}만원 모자람"
    elif revenue < cost.target:
        level = "warn"
        text = f"흑자지만 목표(목표이익 포함)에 {(cost.target - revenue) / 1e4:,.0f}만원 모자람"
    else:
        level = "good"
        text = "목표 달성"
    if base:
        text += f" · {vs_text}"
        if vs <= -DROP_WARNING:
            level = "bad" if level == "bad" else "warn"
            text += " — 원청 물량 확인"
    return MonthCheck(ym, revenue, vs, profit, level, text)


def summary_lines(r: Review, cost: CostModel | None) -> list[str]:
    """화면 위·아래에 띄울 두 줄 — 기준 요약과 시작 달부터의 누적."""
    lines = []
    b = r.baseline
    if b:
        profit = f", 월평균 손익 {b.avg_profit / 1e4:+,.0f}만원" if b.avg_profit is not None else ""
        lines.append(f"기준 {b.first}~{b.last} ({b.months}개월): 월평균 매출 "
                     f"{b.avg_revenue / 1e4:,.0f}만원{profit} · 가장 낮은 달 {b.low[0]} "
                     f"{b.low[1] / 1e4:,.0f}만원")
    else:
        lines.append(f"{r.start} 이전에 끝난 달이 없어 기준을 못 잡았습니다.")

    done = r.closed
    if not done:
        lines.append(f"{r.start}부터 끝난 달이 아직 없습니다. 그 달 매출을 장부에 적고 "
                     "달이 끝나면 여기서 기준과 견줍니다.")
        return lines
    total = sum(c.revenue for c in done)
    text = f"{r.start}부터 {len(done)}개월 누적 매출 {total / 1e4:,.0f}만원"
    if cost:
        profit = sum(c.profit for c in done if c.profit is not None)
        gap = cost.target * len(done) - total
        text += f", 손익 {profit / 1e4:+,.0f}만원"
        text += (f", 목표보다 {gap / 1e4:,.0f}만원 모자람" if gap > 0
                 else f", 목표보다 {-gap / 1e4:,.0f}만원 많음")
    lines.append(text)
    return lines
