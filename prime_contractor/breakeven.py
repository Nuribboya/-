"""손익분기 매출 — '이 밑으로 가면 적자' 선.

평균이나 '작년보다 10%'는 과거를 보고 만든 숫자라 근거가 약하다. 매달 나가는
고정비를 덮으려면 얼마를 팔아야 하는지가 목표의 바닥이다.

    공헌이익률   = 1 − 변동비율 (재료비·외주비처럼 매출에 따라 느는 비용)
    손익분기 매출 = 월 고정비 ÷ 공헌이익률
    목표 매출    = (월 고정비 + 월 목표이익) ÷ 공헌이익률
    그 달 손익   = 매출 × 공헌이익률 − 월 고정비

고정비는 매달 똑같이 나가므로 이 선은 달마다 같다. 평균 기준 목표와 달리
이 선 밑의 달은 '헛경보'가 아니라 실제로 적자인 달이다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    monthly_fixed: int            # 월 고정비 (인건비·임차료·경비·이자)
    variable_ratio: float         # 변동비율 0~1 (재료비·외주비 ÷ 매출)
    monthly_profit: int = 0       # 월 목표이익 (없으면 손익분기가 곧 목표)

    def __post_init__(self) -> None:
        if self.monthly_fixed < 0:
            raise ValueError("월 고정비는 0 이상이어야 합니다.")
        if not 0 <= self.variable_ratio < 1:
            raise ValueError("재료·외주비 비율은 0%~100% 미만이어야 합니다. "
                             "100% 이상이면 팔수록 손해라 손익분기가 없습니다.")

    @property
    def margin_ratio(self) -> float:
        """공헌이익률 — 매출 1원이 고정비를 갚는 데 쓰이는 몫."""
        return 1.0 - self.variable_ratio

    @property
    def breakeven(self) -> int:
        return round(self.monthly_fixed / self.margin_ratio)

    @property
    def target(self) -> int:
        return round((self.monthly_fixed + self.monthly_profit) / self.margin_ratio)

    def profit_at(self, revenue: int) -> int:
        """그 매출이면 한 달 손익이 얼마인가 (음수면 적자)."""
        return round(revenue * self.margin_ratio - self.monthly_fixed)


def from_financials(annual_revenue: int, cost_of_sales: int, sga: int,
                    fixed_share_of_cost_of_sales: float = 0.0) -> CostModel:
    """손익계산서 세 숫자로 대략의 비용 구조를 만든다.

    제조업 매출원가에는 재료비만 있는 게 아니라 공장 인건비·감가상각 같은
    고정비도 섞여 있다. 그대로 쓰면 변동비율이 부풀고 고정비가 줄어든다.
    `fixed_share_of_cost_of_sales` 로 매출원가 중 고정비 몫을 옮길 수 있다.

    주의: 흑자 회사라면 이 치우침은 손익분기를 실제보다 **낮게** 만든다.
    손익분기 = 매출 − 이익 ÷ 공헌이익률 이라, 공헌이익률을 작게 잡을수록
    손익분기가 내려간다. 즉 '안전하다'고 잘못 알려주는 쪽으로 틀린다.
    (적자 회사라면 반대로 높게 나온다.)
    """
    if annual_revenue <= 0:
        raise ValueError("연 매출이 0보다 커야 합니다.")
    if not 0 <= fixed_share_of_cost_of_sales < 1:
        raise ValueError("매출원가 중 고정비 몫은 0~1 사이여야 합니다.")
    fixed_in_cogs = cost_of_sales * fixed_share_of_cost_of_sales
    variable = cost_of_sales - fixed_in_cogs
    return CostModel(
        monthly_fixed=round((sga + fixed_in_cogs) / 12),
        variable_ratio=variable / annual_revenue,
    )


def parse_ratio(text: str) -> float:
    """'60', '60%', '0.6' 모두 0.6 으로."""
    cleaned = str(text).replace("%", "").replace(",", "").strip()
    value = float(cleaned)
    return value / 100 if value > 1 else value
