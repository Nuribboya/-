"""장부 숫자로 '회사 안쪽에서 뭐부터 챙길지' 우선순위 매기기.

원청 후보를 늘리는 영업 확대는 pipeline/goal 쪽에서 이미 다룬다. 여기는
반대쪽 — 비용을 줄일지, 사람을 어떻게 쓸지 — 를 매출 장부와 손익분기
숫자만 보고 순서를 매긴다. 숫자 몇 개로 판단하는 어림값이라 '결정'이
아니라 '어디부터 볼지'다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from prime_contractor.breakeven import CostModel
from prime_contractor.sales import SalesBook

#: 재료·외주비가 매출의 이 비율을 넘으면 원가 쪽을 먼저 보라고 권한다.
HIGH_VARIABLE_RATIO = 0.70
#: 직원당 매출이 이전 대비 이만큼(비율) 넘게 변하면 '늘었다/줄었다'로 본다.
REVENUE_PER_HEAD_MOVE = 0.10
#: 직원당 매출 추세를 볼 때 최근 몇 달 평균끼리 비교할지.
TREND_WINDOW = 3

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Priority:
    text: str
    reason: str
    severity: str   # "high" | "medium" | "low"


@dataclass
class Diagnosis:
    revenue_per_employee: int | None = None
    revenue_per_employee_prior: int | None = None
    revenue_per_employee_trend: str = ""     # "증가" | "감소" | "보합" | ""
    latest_month: str = ""
    latest_month_profit: int | None = None
    priorities: list[Priority] = field(default_factory=list)
    note: str = ""


def analyze(book: SalesBook, employees: int, cost: CostModel | None = None,
           window: int = TREND_WINDOW, today: date | None = None) -> Diagnosis:
    """끝난 달의 매출·손익·직원당 매출로 진단을 만든다.

    진행 중인 달은 아직 다 안 찍힌 숫자라 뺀다(다른 매출 계산과 동일한 규칙).
    """
    diag = Diagnosis()
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    closed = [m for m in book.sorted_months() if m.ym < current and m.revenue]
    if not closed:
        diag.note = "끝난 달의 매출 기록이 없어 진단할 수 없습니다."
        return diag

    latest = closed[-1]
    diag.latest_month = latest.ym
    if cost:
        diag.latest_month_profit = cost.profit_at(latest.revenue)

    if employees > 0:
        recent = closed[-window:]
        diag.revenue_per_employee = int(sum(m.revenue for m in recent) / len(recent) / employees)
        prior = closed[-2 * window:-window]
        if prior:
            diag.revenue_per_employee_prior = int(sum(m.revenue for m in prior) / len(prior) / employees)
            if diag.revenue_per_employee_prior:
                change = ((diag.revenue_per_employee - diag.revenue_per_employee_prior)
                          / diag.revenue_per_employee_prior)
                if change <= -REVENUE_PER_HEAD_MOVE:
                    diag.revenue_per_employee_trend = "감소"
                elif change >= REVENUE_PER_HEAD_MOVE:
                    diag.revenue_per_employee_trend = "증가"
                else:
                    diag.revenue_per_employee_trend = "보합"

    diag.priorities = _rank(diag, cost)
    return diag


def _rank(diag: Diagnosis, cost: CostModel | None) -> list[Priority]:
    items: list[Priority] = []

    if diag.latest_month_profit is not None and diag.latest_month_profit < 0:
        items.append(Priority(
            text="비용부터 줄이기",
            reason=f"{diag.latest_month} 매출로는 약 {abs(diag.latest_month_profit) / 1e4:,.0f}만원 "
                   "적자로 잡힙니다. 매출을 늘리는 것보다 고정비·변동비를 줄이는 쪽이 더 급합니다.",
            severity="high",
        ))

    if cost and cost.variable_ratio >= HIGH_VARIABLE_RATIO:
        items.append(Priority(
            text="원가(재료·외주비) 재협상",
            reason=f"매출의 {cost.variable_ratio * 100:.0f}%가 재료·외주비입니다. "
                   "이 비중이 높으면 매출이 늘어도 남는 돈은 잘 안 늘어납니다.",
            severity="medium",
        ))

    if diag.revenue_per_employee_trend == "감소":
        items.append(Priority(
            text="인력 재배치 — 신규 채용은 보류",
            reason=f"직원 1인당 매출이 최근 {TREND_WINDOW}개월 평균 "
                   f"{diag.revenue_per_employee / 1e4:,.0f}만원으로 이전보다 줄었습니다. "
                   "사람을 늘리기 전에 지금 인력을 어디에 쓰고 있는지부터 보는 게 우선입니다.",
            severity="medium",
        ))
    elif diag.revenue_per_employee_trend == "증가":
        items.append(Priority(
            text="증원 여력 검토",
            reason=f"직원 1인당 매출이 늘고 있습니다({diag.revenue_per_employee / 1e4:,.0f}만원/인). "
                   "일감이 사람보다 많다면 증원을 검토할 시점입니다.",
            severity="low",
        ))

    if not items:
        items.append(Priority(
            text="영업 확대 — 원청 후보 늘리기",
            reason="지금 숫자만 보면 특별히 위험한 신호는 없습니다. "
                   "'① 일감 줄 회사 찾기'로 새 원청 후보를 넓히는 데 집중해도 됩니다.",
            severity="low",
        ))

    items.sort(key=lambda p: _SEVERITY_ORDER[p.severity])
    return items
