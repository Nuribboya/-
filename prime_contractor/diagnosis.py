"""장부 숫자로 '회사 안쪽에서 뭐부터 챙길지' 우선순위 매기기.

원청 후보를 늘리는 영업 확대는 pipeline/goal 쪽에서 이미 다룬다. 여기는
반대쪽 — 비용을 줄일지, 사람을 어떻게 쓸지 — 를 매출 장부와 손익분기
숫자만 보고 순서를 매긴다. 숫자 몇 개로 판단하는 어림값이라 '결정'이
아니라 '어디부터 볼지'다.

주의: 직원 수는 장부처럼 달마다 있는 값이 아니라 화면에서 한 번 받는
'지금' 값이다. 그래서 '직원당 매출'의 추세는 사실상 매출 자체의 추세와
같다 — 같은 수로 나눈 값이 늘고 주는 건 분자(매출)가 늘고 줄기 때문이다.
매출이 준 걸 '인력이 넘친다'고 넘겨짚으면, 실제로는 원청 발주량이 준
것뿐인데 인력을 줄이라고 잘못 권하게 된다. 그래서 '매출 자체가 줄었나'와
'직원당 매출'을 분리해서 본다 — 전자만 우선순위를 만들고, 후자는 참고용
숫자로만 보여준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from prime_contractor.breakeven import CostModel
from prime_contractor.sales import SalesBook

#: 재료·외주비가 매출의 이 비율을 넘으면 원가 쪽을 먼저 보라고 권한다.
HIGH_VARIABLE_RATIO = 0.70
#: 매출(또는 직원당 매출)이 이전 대비 이만큼(비율) 넘게 변하면 '늘었다/줄었다'로 본다.
REVENUE_MOVE = 0.10
#: 추세를 볼 때 최근 몇 달 평균끼리 비교할지.
TREND_WINDOW = 3

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Priority:
    text: str
    reason: str
    severity: str   # "high" | "medium" | "low"


@dataclass
class Diagnosis:
    revenue_trend: str = ""                  # "증가" | "감소" | "보합" | "" — 매출 자체의 추세
    revenue_per_employee: int | None = None  # 참고용. 우선순위 판단엔 안 쓴다
    revenue_per_employee_trend: str = ""
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

    recent = closed[-window:]
    prior = closed[-2 * window:-window]
    recent_avg = sum(m.revenue for m in recent) / len(recent)
    if prior:
        prior_avg = sum(m.revenue for m in prior) / len(prior)
        if prior_avg:
            diag.revenue_trend = _trend(recent_avg, prior_avg)

    if employees > 0:
        diag.revenue_per_employee = int(recent_avg / employees)
        diag.revenue_per_employee_trend = diag.revenue_trend    # 참고용 — 같은 추세, 다른 단위일 뿐

    diag.priorities = _rank(diag, cost)
    return diag


def _trend(recent_avg: float, prior_avg: float) -> str:
    change = (recent_avg - prior_avg) / prior_avg
    if change <= -REVENUE_MOVE:
        return "감소"
    if change >= REVENUE_MOVE:
        return "증가"
    return "보합"


def _rank(diag: Diagnosis, cost: CostModel | None) -> list[Priority]:
    items: list[Priority] = []

    if diag.latest_month_profit is not None and diag.latest_month_profit < 0:
        items.append(Priority(
            text="비용부터 줄이기",
            reason=f"{diag.latest_month} 매출로는 약 {abs(diag.latest_month_profit) / 1e4:,.0f}만원 "
                   "적자로 잡힙니다. 매출을 늘리는 것보다 고정비·변동비를 줄이는 쪽이 더 급합니다.",
            severity="high",
        ))

    if diag.revenue_trend == "감소":
        items.append(Priority(
            text="영업 대응 — 일감이 줄고 있습니다",
            reason=f"최근 {TREND_WINDOW}개월 매출 평균이 그 이전 {TREND_WINDOW}개월보다 줄었습니다. "
                   "인력이 넘쳐서가 아니라 원청 발주량 자체가 준 것일 가능성이 큽니다. "
                   "원청에 물량부터 확인하고, 필요하면 '① 일감 줄 회사 찾기'로 새 원청 후보를 넓히세요.",
            severity="medium",
        ))

    if cost and cost.variable_ratio >= HIGH_VARIABLE_RATIO:
        items.append(Priority(
            text="원가(재료·외주비) 재협상",
            reason=f"매출의 {cost.variable_ratio * 100:.0f}%가 재료·외주비입니다. "
                   "이 비중이 높으면 매출이 늘어도 남는 돈은 잘 안 늘어납니다.",
            severity="medium",
        ))

    if diag.revenue_trend == "증가" and diag.revenue_per_employee is not None:
        items.append(Priority(
            text="증원 여력 검토",
            reason=f"매출이 늘면서 직원 1인당 매출도 {diag.revenue_per_employee / 1e4:,.0f}만원까지 "
                   "올랐습니다. 지금 인력으로 이 물량을 계속 감당할 수 있는지 확인해 보세요.",
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
