"""원청 적합도 평가.

'점수 72점'만 던지면 어디부터 연락해야 할지 알 수 없다. 그래서 다섯 축으로
나눠 각각 몇 점인지, 왜 그런지, 추정 판넬 물량이 얼마인지까지 같이 낸다.

축                        배점   묻는 것
------------------------  ----  ------------------------------------------
product_fit (판넬 일감)     30   우리가 만드는 물건이 실제로 들어가는 일인가
volume      (일감 크기)     25   그래서 판넬이 얼마어치나 나오는가
access      (거리)          20   안성에서 대응할 만한 거리인가
repeat      (꾸준함)        15   한 번 뚫으면 계속 나오는가
safety      (안전)          10   기존 원청과 부딪히지 않고, 정보가 믿을 만한가
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from prime_contractor.models import Candidate
from prime_contractor.textutil import pad

# --- 우리 품목이 얼마나 직접적으로 걸리는가 -------------------------------------
#
# 공고명에 '배전반'이 있으면 판넬이 확실히 들어간다. '전기공사'만 있으면 들어갈
# 수도 아닐 수도 있다. 이 차이를 점수에 반영한다.
DIRECT_PRODUCT = (
    "제어반", "배전반", "수배전반", "분전반", "MCC", "고압반", "저압반",
    "큐비클", "계장반", "판넬", "패널", "전기실", "변전실", "수변전",
)
PROCESS_KEYWORDS = (
    "자동제어", "계장", "감시제어", "원격감시", "SCADA", "PLC", "전기계장",
    "자동화설비", "특고압", "변압기", "차단기", "UPS", "비상발전",
)
GENERAL_KEYWORDS = ("전기공사", "기계설비공사", "전력설비", "설비공사")

#: 낙찰금액 중 판넬이 차지하는 대략의 비중. 업계 통념 수준의 어림값이라
#: 실제 견적과는 다르다. 순위를 매기기 위한 상대 지표로만 쓴다.
PANEL_SHARE = {
    ("물품", "direct"): 0.70,    # 판넬 자체를 사는 계약
    ("물품", "process"): 0.35,
    ("공사", "direct"): 0.25,
    ("공사", "process"): 0.15,
    ("용역", "direct"): 0.15,
    ("용역", "process"): 0.10,
    ("기타", "general"): 0.05,
}
DEFAULT_SHARE = 0.05

GRADE_CUTS = ((75, "A"), (60, "B"), (45, "C"), (0, "D"))

# --- 규모 맞음 -----------------------------------------------------------------
#
# '일감 크기' 축은 클수록 점수를 준다. 그런데 우리 월매출이 1억 안팎이면,
# 한 달에 판넬 50만원어치 주는 곳은 영업 들인 만큼 안 남고, 한 달에 2억어치
# 주는 곳은 감당이 안 되거나 그 한 곳에 새로 매이게 된다. 우리 매출을 알면
# '클수록 좋다' 대신 '우리 크기에 맞나'로 본다.
#
# (그 곳의 월 판넬 물량 ÷ 우리 월매출, 점수) — 사이는 직선으로 잇는다.
SCALE_CURVE = (
    (0.00, 0), (0.03, 30), (0.10, 100), (0.40, 100),
    (0.80, 50), (1.50, 15), (3.00, 10),
)
SWEET_SPOT = (0.10, 0.40)
#: 한 건의 판넬 몫이 우리 월매출의 몇 배를 넘으면 자재 선투입 경고를 붙이나
BIG_JOB_MONTHS = 1.5
GRADE_ADVICE = {
    "A": "먼저 연락해 보세요",
    "B": "연락해 볼 만합니다",
    "C": "여유 있을 때",
    "D": "지금은 아닙니다",
}


@dataclass
class Axis:
    key: str
    label: str
    score: float          # 0~100 (축 내부 비율)
    weight: float         # 총점에서 차지하는 배점
    detail: str = ""      # 사람이 읽는 근거

    @property
    def points(self) -> float:
        return round(self.score / 100 * self.weight, 1)


@dataclass
class Fitness:
    total: float = 0.0
    grade: str = "D"
    axes: list[Axis] = field(default_factory=list)
    headline: str = ""
    cautions: list[str] = field(default_factory=list)
    est_panel_amount: int = 0      # 추정 판넬 물량(원)

    @property
    def advice(self) -> str:
        return GRADE_ADVICE[self.grade]

    def axis(self, key: str) -> Axis | None:
        return next((a for a in self.axes if a.key == key), None)

    def explain(self) -> str:
        lines = [f"{self.grade}등급 · {self.total:.1f}점 — {self.advice}", f"  {self.headline}", ""]
        lines.append("  왜 이 점수인가")
        for a in self.axes:
            lines.append(f"   · {pad(a.label, 11)} {a.points:5.1f}점 (최대 {a.weight:2.0f}점)   {a.detail}")
        if self.cautions:
            lines.append("")
            lines.append("  확인하실 점")
            for c in self.cautions:
                lines.append(f"   · {c}")
        return "\n".join(lines)


def _match_level(title: str) -> str:
    """공고 하나가 우리 품목과 얼마나 가까운가."""
    up = title.upper()
    if any(k.upper() in up for k in DIRECT_PRODUCT):
        return "direct"
    if any(k.upper() in up for k in PROCESS_KEYWORDS):
        return "process"
    if any(k.upper() in up for k in GENERAL_KEYWORDS):
        return "general"
    return "none"


def estimate_panel_amount(cand: Candidate) -> int:
    """낙찰 건별로 판넬 비중을 곱해 더한다. 어디까지나 어림값이다."""
    total = 0.0
    for a in cand.awards:
        level = _match_level(a.title)
        if level == "none":
            continue
        share = PANEL_SHARE.get((a.category, level), DEFAULT_SHARE)
        total += a.amount * share
    return int(total)


def _product_fit(cand: Candidate) -> Axis:
    levels = [_match_level(a.title) for a in cand.awards]
    direct = levels.count("direct")
    process = levels.count("process")
    general = levels.count("general")

    if not cand.awards:
        # 낙찰 이력이 없는 후보(업종 훑기 모드)는 업종 적합도로 대신 본다.
        score = min(cand.sector_weight / 2.0, 1.0) * 70
        return Axis("product_fit", "판넬 일감", score, 30.0,
                    f"수주 기록이 없어 업종({cand.sector or '미분류'})만 보고 매긴 점수입니다")

    if direct:
        score = min(60 + direct * 20, 100)
        detail = f"공고에 배전반·제어반이 직접 적힌 일 {direct}건 — 판넬이 확실히 들어갑니다"
    elif process:
        score = min(40 + process * 10, 75)
        detail = f"자동제어·계장 공사 {process}건 — 판넬이 들어갈 가능성이 높습니다"
    elif general:
        score = min(20 + general * 5, 40)
        detail = f"일반 전기·설비 공사 {general}건 — 판넬이 들어가는지는 확인이 필요합니다"
    else:
        score = 10
        detail = "우리가 만드는 물건과 연결되는 공사가 안 보입니다"
    return Axis("product_fit", "판넬 일감", score, 30.0, detail)


def _volume(cand: Candidate, est: int) -> Axis:
    if est <= 0:
        return Axis("volume", "일감 크기", 0.0, 25.0, "판넬이 들어갈 만한 일이 안 보입니다")
    eok = est / 1e8
    # 10억이면 만점. 로그라 대형 1건이 전부를 먹지 않는다.
    score = min(math.log1p(eok) / math.log(11) * 100, 100)
    return Axis("volume", "일감 크기", score, 25.0,
                f"공사비 {cand.award_amount / 1e8:.1f}억 중 판넬 몫을 약 {eok:.1f}억으로 봅니다")


def _access(cand: Candidate, max_km: float) -> Axis:
    if cand.distance_km is None:
        return Axis("access", "거리", 35.0, 20.0, "주소를 못 찾았습니다 — 직접 확인해 보세요")
    if cand.distance_km >= max_km:
        return Axis("access", "거리", 0.0, 20.0, f"{cand.region} {cand.distance_km:.0f}km — 다니기엔 먼 거리입니다")
    score = (1 - cand.distance_km / max_km) * 100
    return Axis("access", "거리", score, 20.0, f"{cand.region} {cand.distance_km:.0f}km")


def _repeat(cand: Candidate) -> Axis:
    """한 번 뚫으면 계속 나올 곳인가. 건수와 '거래처가 여럿인지'를 본다."""
    count = cand.award_count
    if count == 0:
        return Axis("repeat", "꾸준함", 0.0, 15.0, "수주 기록이 없습니다")
    if count == 1:
        return Axis("repeat", "꾸준함", 25.0, 15.0, "1건뿐이라 계속 일이 나올지는 아직 모릅니다")
    orgs = len({a.demand_org for a in cand.awards if a.demand_org})
    count_part = min(math.log1p(count) / math.log(7), 1.0)        # 6건이면 만점
    org_part = min(orgs / 3, 1.0)                                  # 발주처 3곳이면 만점
    score = (0.65 * count_part + 0.35 * org_part) * 100
    return Axis("repeat", "꾸준함", score, 15.0, f"{count}건을 {orgs}곳에서 받았습니다")


def _safety(cand: Candidate) -> Axis:
    """기존 원청과 부딪힐 위험 + 판정 근거의 확실성."""
    level = cand.overlap.level if cand.overlap else "clear"
    base = {"clear": 100.0, "adjacent": 70.0, "same_industry": 45.0, "affiliate": 0.0}[level]
    known = sum(bool(v) for v in (cand.ksic_code, cand.bizno, cand.address))
    score = base * (0.6 + 0.4 * known / 3)
    label = cand.overlap.label if cand.overlap else "무관"
    return Axis("safety", "안전", score, 10.0, f"기존 원청과 {label} · 확인된 회사 정보 {known}/3가지")


def _headline(cand: Candidate, est: int) -> str:
    bits = []
    if cand.region:
        bits.append(f"{cand.region} {cand.distance_km:.0f}km" if cand.distance_km is not None
                    else cand.region)
    if cand.sector:
        bits.append(cand.sector)
    if cand.award_count:
        bits.append(f"{cand.award_count}건 {cand.award_amount / 1e8:.1f}억")
    if est:
        bits.append(f"추정 판넬 {est / 1e8:.1f}억")
    return " · ".join(bits) if bits else "판단할 정보가 부족합니다"


def _cautions(cand: Candidate) -> list[str]:
    out = []
    if cand.status_note:
        out.append(cand.status_note)
    if cand.distance_km is None:
        out.append("주소를 못 찾아 거리를 못 쟀습니다. 회사 위치를 직접 확인해 보세요")
    if not cand.ksic_code:
        out.append("공식 업종 정보가 없어, 회사 이름과 공사명만 보고 업종을 짐작했습니다")
    if cand.award_count == 1:
        out.append("따낸 공사가 1건뿐입니다. 꾸준한 거래처인지 확인이 필요합니다")
    if cand.overlap and cand.overlap.level == "same_industry":
        out.append("케이씨그룹과 같은 분야입니다. 기존 거래에 문제가 없을지 한번 보세요")
    if cand.kind == "demand_org":
        out.append("관공서·공공기관이라 공사를 맡은 회사를 거쳐야 할 수 있습니다")
    return out


def _curve(x: float, points=SCALE_CURVE) -> float:
    if x <= points[0][0]:
        return float(points[0][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return float(points[-1][1])


def monthly_panel(est: int, lookback_days: int) -> int:
    """조회 기간 전체의 판넬 물량을 한 달치로."""
    return round(est / max(lookback_days / 30.0, 1.0)) if est else 0


def largest_job_panel(cand: Candidate) -> int:
    """가장 큰 한 건에서 나오는 판넬 몫."""
    best = 0
    for a in cand.awards:
        level = _match_level(a.title)
        if level == "none":
            continue
        best = max(best, int(a.amount * PANEL_SHARE.get((a.category, level), DEFAULT_SHARE)))
    return best


def _scale(cand: Candidate, est: int, our_monthly: int, lookback_days: int) -> Axis:
    per_month = monthly_panel(est, lookback_days)
    if not cand.awards:
        return Axis("scale", "규모 맞음", 50.0, 25.0,
                    "수주 기록이 없어 크기를 모릅니다 — 중간으로 둡니다")
    if per_month <= 0:
        return Axis("scale", "규모 맞음", 0.0, 25.0, "판넬 들어갈 일이 안 보여 크기를 잴 수 없습니다")

    ratio = per_month / our_monthly
    score = _curve(ratio)
    amount = f"한 달 판넬 약 {per_month / 1e4:,.0f}만원 = 우리 월매출의 {ratio * 100:.0f}%"
    low, high = SWEET_SPOT
    if ratio < low:
        why = "작아서 영업 들인 만큼 남기 어렵습니다"
    elif ratio <= high:
        why = "무리 없이 받으면서 의존도도 낮추기 좋은 크기입니다"
    elif ratio <= 1.0:
        why = "지금 인력·자금으로는 빠듯한 크기입니다"
    else:
        why = "이 한 곳이 지금 매출보다 커서, 또 한 곳에 매이게 됩니다"
    return Axis("scale", "규모 맞음", score, 25.0, f"{amount} — {why}")


def evaluate(cand: Candidate, max_distance_km: float = 150.0,
             weights: dict[str, float] | None = None,
             our_monthly_revenue: int = 0, lookback_days: int = 180) -> Fitness:
    """후보 하나의 적합도를 낸다.

    우리 월매출을 알면 '일감 크기'(클수록 좋다) 자리에 '규모 맞음'(우리 크기에
    맞나)을 쓴다. 둘을 같이 넣으면 여전히 큰 곳이 유리해진다.
    """
    est = estimate_panel_amount(cand)
    size_axis = (_scale(cand, est, our_monthly_revenue, lookback_days)
                 if our_monthly_revenue > 0 else _volume(cand, est))
    axes = [_product_fit(cand), size_axis, _access(cand, max_distance_km),
            _repeat(cand), _safety(cand)]
    if weights:
        axes = [Axis(a.key, a.label, a.score, weights.get(a.key, a.weight), a.detail) for a in axes]

    total = sum(a.points for a in axes)
    # 배점을 바꿔도 100점 만점으로 읽히게 정규화한다.
    span = sum(a.weight for a in axes) or 1.0
    total = round(total / span * 100, 1)
    grade = next(g for cut, g in GRADE_CUTS if total >= cut)
    cautions = _cautions(cand)
    if our_monthly_revenue > 0:
        biggest = largest_job_panel(cand)
        # 한 달 매출보다 조금 큰 건은 공공 공사에선 흔하다. 거기까지 경고하면
        # 모든 후보에 붙어 잡음이 된다(연습 자료에서 6곳 모두에 붙었다).
        if biggest > our_monthly_revenue * BIG_JOB_MONTHS:
            cautions.append(f"가장 큰 한 건의 판넬만 약 {biggest / 1e8:.1f}억 — 우리 한 달 매출의 "
                            f"{biggest / our_monthly_revenue:.1f}배입니다. 자재를 먼저 사 넣을 "
                            f"돈이 되는지부터 보세요")
    return Fitness(total=total, grade=grade, axes=axes,
                   headline=_headline(cand, est), cautions=cautions,
                   est_panel_amount=est)
