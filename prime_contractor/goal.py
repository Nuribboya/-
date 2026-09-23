"""최우선 목표 — 원청 한 곳 의존도를 낮춘다.

원청이 한 곳뿐이면 매출을 늘리는 것보다 '그 한 곳이 흔들려도 버티는 것'이
먼저다. 목표를 의존도로 잡고, 거기서 거꾸로 계산해 이번 주에 몇 곳에
연락해야 하는지까지 내린다.

    필요한 신규 매출(월) = 현재 월매출 × (1 − 목표 의존도) ÷ 목표 의존도
    필요한 신규 원청 수   = 필요한 신규 매출 ÷ 원청 한 곳당 월 발주
    연락해야 할 곳 수     = 필요한 신규 원청 수 ÷ (연락 → 첫 수주 전환율)
    주당 연락 수          = 연락해야 할 곳 수 ÷ 남은 주

전환율과 '원청 한 곳당 월 발주'는 어림값이다. 실제 기록이 쌓이면 그 숫자로
바꾸는 게 맞다 — 영업 진행 기록(leads.py)에서 실제 전환율을 계산해 준다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

#: 단계별 넘어가는 비율 기본값. 처음 거래를 트는 B2B 영업의 어림값이다.
DEFAULT_FUNNEL = {
    "연락 → 미팅": 0.30,
    "미팅 → 협력업체 등록": 0.40,
    "등록 → 첫 수주": 0.50,
}


@dataclass
class GoalInputs:
    monthly_revenue: int              # 지금 월매출 (끝난 달 평균)
    target_dependency: float = 0.70   # 목표: 가장 큰 원청 비중을 이 밑으로
    months: int = 12                  # 기한
    revenue_per_new_client: int = 10_000_000   # 새 원청 한 곳이 초기에 주는 월 발주
    current_dependency: float = 1.0   # 지금 가장 큰 원청 비중 (원청 한 곳이면 100%)
    funnel: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FUNNEL))
    breakeven: int = 0                # 손익분기 매출(월). 0 이면 위험 계산 생략
    margin_ratio: float = 0.0         # 공헌이익률 (손익분기 모델에서)
    monthly_fixed: int = 0            # 월 고정비
    cash_on_hand: int = 0             # 지금 쓸 수 있는 현금 (선택)

    def __post_init__(self) -> None:
        if not 0 < self.target_dependency < 1:
            raise ValueError("목표 의존도는 0%~100% 사이여야 합니다 (예: 70).")
        if not 0 < self.current_dependency <= 1:
            raise ValueError("지금 의존도는 0%~100% 사이여야 합니다.")
        if self.months <= 0:
            raise ValueError("기한은 1개월 이상이어야 합니다.")
        if self.monthly_revenue <= 0:
            raise ValueError("월매출이 0보다 커야 합니다.")
        if self.revenue_per_new_client <= 0:
            raise ValueError("새 원청 한 곳당 월 발주는 0보다 커야 합니다.")
        for step, rate in self.funnel.items():
            if not 0 < rate <= 1:
                raise ValueError(f"'{step}' 전환율은 0%~100% 사이여야 합니다.")

    @property
    def close_rate(self) -> float:
        """연락한 곳 중 첫 수주까지 가는 비율."""
        return math.prod(self.funnel.values())


@dataclass
class GoalPlan:
    inputs: GoalInputs
    anchor_revenue: int               # 지금 가장 큰 원청에서 나오는 월매출
    new_revenue_needed: int           # 다른 곳에서 새로 벌어야 할 월매출
    clients_needed: int               # 새로 뚫어야 할 원청 수
    contacts_needed: int              # 연락해야 할 곳 수
    registrations_needed: int         # 협력업체 등록까지 가야 할 곳 수
    weeks: int
    contacts_per_week: float
    loss_if_anchor_stops: int = 0     # 그 원청이 멈추면 매달 손실
    months_of_runway: float | None = None
    already_there: bool = False

    def summary(self) -> list[str]:
        i = self.inputs
        if self.already_there:
            return [f"지금 가장 큰 원청 비중이 {i.current_dependency * 100:.0f}% 로 "
                    f"이미 목표({i.target_dependency * 100:.0f}%) 밑입니다."]
        lines = [
            f"목표: {i.months}개월 안에 가장 큰 원청 비중 "
            f"{i.current_dependency * 100:.0f}% → {i.target_dependency * 100:.0f}%",
            "",
            f"  그러려면 다른 곳에서 월 {self.new_revenue_needed / 1e4:,.0f}만원을 새로 벌어야 합니다",
            f"  → 새 원청 {self.clients_needed}곳 "
            f"(한 곳당 월 {i.revenue_per_new_client / 1e4:,.0f}만원 가정)",
            f"  → 협력업체 등록 {self.registrations_needed}곳",
            f"  → 연락 {self.contacts_needed}곳 "
            f"(연락한 곳 중 {i.close_rate * 100:.0f}% 가 첫 수주까지 간다고 가정)",
            "",
            f"  ▶ 이번 주 목표: {math.ceil(self.contacts_per_week)}곳에 연락",
            f"    ({self.weeks}주 동안 주 {self.contacts_per_week:.1f}곳)",
        ]
        if self.loss_if_anchor_stops:
            lines += ["", "위험"]
            lines.append(f"  지금 원청이 발주를 멈추면 매달 약 "
                         f"{self.loss_if_anchor_stops / 1e4:,.0f}만원 적자입니다")
            if self.months_of_runway is not None:
                lines.append(f"  지금 현금으로 약 {self.months_of_runway:.1f}개월 버팁니다")
        return lines


def build_plan(inputs: GoalInputs) -> GoalPlan:
    i = inputs
    anchor = round(i.monthly_revenue * i.current_dependency)
    others_now = i.monthly_revenue - anchor
    weeks = max(1, round(i.months * 52 / 12))

    # 원청 매출은 그대로 두고 다른 곳을 키워 비중을 낮춘다고 본다.
    # 원청 매출 ÷ 전체 = 목표  →  필요한 전체 = 원청 ÷ 목표
    total_needed = anchor / i.target_dependency
    new_needed = max(0, round(total_needed - anchor - others_now))
    already = i.current_dependency <= i.target_dependency or new_needed == 0

    clients = 0 if already else math.ceil(new_needed / i.revenue_per_new_client)
    reg_rate = i.funnel.get("등록 → 첫 수주", 1.0)
    registrations = 0 if already else math.ceil(clients / reg_rate)
    contacts = 0 if already else math.ceil(clients / i.close_rate)

    loss = 0
    runway = None
    if i.monthly_fixed and i.margin_ratio:
        # 원청이 멈추면 남는 매출로 고정비를 얼마나 덮는지
        remaining_margin = others_now * i.margin_ratio
        loss = max(0, round(i.monthly_fixed - remaining_margin))
        if i.cash_on_hand and loss:
            runway = i.cash_on_hand / loss

    return GoalPlan(
        inputs=i, anchor_revenue=anchor, new_revenue_needed=new_needed,
        clients_needed=clients, contacts_needed=contacts,
        registrations_needed=registrations, weeks=weeks,
        contacts_per_week=contacts / weeks if weeks else 0,
        loss_if_anchor_stops=loss, months_of_runway=runway, already_there=already)
