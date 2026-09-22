"""원청 적합도 평가.

'점수 72점'만 던지면 어디부터 연락해야 할지 알 수 없다. 그래서 다섯 축으로
나눠 각각 몇 점인지, 왜 그런지, 추정 판넬 물량이 얼마인지까지 같이 낸다.

축                    배점   묻는 것
--------------------  ----  ----------------------------------------------
product_fit (품목)      30   우리가 만드는 물건이 실제로 들어가는 일인가
volume      (물량)      25   그래서 판넬이 얼마어치나 나오는가
access      (접근성)    20   안성에서 대응할 만한 거리인가
repeat      (지속성)    15   한 번 뚫으면 계속 나오는가
safety      (안전도)    10   기존 원청과 부딪히지 않고, 정보가 믿을 만한가
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from prime_contractor.models import Candidate

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
GRADE_ADVICE = {
    "A": "우선 접촉",
    "B": "접촉 가치 있음",
    "C": "여력 될 때",
    "D": "보류",
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
        lines = [f"{self.grade}등급 {self.total:.1f}점 — {self.advice}", f"  {self.headline}"]
        for a in self.axes:
            lines.append(f"  · {a.label:<8} {a.points:5.1f} / {a.weight:.0f}   {a.detail}")
        for c in self.cautions:
            lines.append(f"  ⚠ {c}")
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
        return Axis("product_fit", "품목", score, 30.0,
                    f"수주 이력 없음, 업종({cand.sector or '미분류'})으로 추정")

    if direct:
        score = min(60 + direct * 20, 100)
        detail = f"판넬 품목 직접 명시 {direct}건"
    elif process:
        score = min(40 + process * 10, 75)
        detail = f"자동제어·계장 공정 {process}건"
    elif general:
        score = min(20 + general * 5, 40)
        detail = f"일반 전기·설비 공사 {general}건 (판넬 포함 여부 확인 필요)"
    else:
        score = 10
        detail = "우리 품목과 직접 연결되는 공고 없음"
    return Axis("product_fit", "품목", score, 30.0, detail)


def _volume(cand: Candidate, est: int) -> Axis:
    if est <= 0:
        return Axis("volume", "물량", 0.0, 25.0, "추정 물량 없음")
    eok = est / 1e8
    # 10억이면 만점. 로그라 대형 1건이 전부를 먹지 않는다.
    score = min(math.log1p(eok) / math.log(11) * 100, 100)
    return Axis("volume", "물량", score, 25.0,
                f"추정 판넬 {eok:.1f}억 (낙찰 {cand.award_amount / 1e8:.1f}억 기준)")


def _access(cand: Candidate, max_km: float) -> Axis:
    if cand.distance_km is None:
        return Axis("access", "접근성", 35.0, 20.0, "주소 미확인 — 직접 확인 필요")
    if cand.distance_km >= max_km:
        return Axis("access", "접근성", 0.0, 20.0, f"{cand.region} {cand.distance_km:.0f}km (권역 밖)")
    score = (1 - cand.distance_km / max_km) * 100
    return Axis("access", "접근성", score, 20.0, f"{cand.region} {cand.distance_km:.0f}km")


def _repeat(cand: Candidate) -> Axis:
    """한 번 뚫으면 계속 나올 곳인가. 건수와 '거래처가 여럿인지'를 본다."""
    count = cand.award_count
    if count == 0:
        return Axis("repeat", "지속성", 0.0, 15.0, "수주 이력 없음")
    if count == 1:
        return Axis("repeat", "지속성", 25.0, 15.0, "단발 1건 — 반복 여부 미확인")
    orgs = len({a.demand_org for a in cand.awards if a.demand_org})
    count_part = min(math.log1p(count) / math.log(7), 1.0)        # 6건이면 만점
    org_part = min(orgs / 3, 1.0)                                  # 발주처 3곳이면 만점
    score = (0.65 * count_part + 0.35 * org_part) * 100
    return Axis("repeat", "지속성", score, 15.0, f"{count}건 / 발주처 {orgs}곳")


def _safety(cand: Candidate) -> Axis:
    """기존 원청과 부딪힐 위험 + 판정 근거의 확실성."""
    level = cand.overlap.level if cand.overlap else "clear"
    base = {"clear": 100.0, "adjacent": 70.0, "same_industry": 45.0, "affiliate": 0.0}[level]
    known = sum(bool(v) for v in (cand.ksic_code, cand.bizno, cand.address))
    score = base * (0.6 + 0.4 * known / 3)
    label = cand.overlap.label if cand.overlap else "무관"
    return Axis("safety", "안전도", score, 10.0, f"{label} / 확인된 정보 {known}/3")


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
    return " · ".join(bits) if bits else "근거 부족"


def _cautions(cand: Candidate) -> list[str]:
    out = []
    if cand.distance_km is None:
        out.append("주소를 못 찾아 거리 미반영 — 소재지 직접 확인 필요")
    if not cand.ksic_code:
        out.append("업종코드 미확인 (DART 미등록) — 업종 판정이 상호·공고명 추정")
    if cand.award_count == 1:
        out.append("낙찰 1건뿐 — 지속 거래처인지 확인 필요")
    if cand.overlap and cand.overlap.level == "same_industry":
        out.append("기존 원청과 같은 시장 — 관계 충돌 여부 확인")
    if cand.kind == "demand_org":
        out.append("발주기관이라 시공사를 통해 들어가야 할 수 있음")
    return out


def evaluate(cand: Candidate, max_distance_km: float = 150.0,
             weights: dict[str, float] | None = None) -> Fitness:
    """후보 하나의 적합도를 낸다."""
    est = estimate_panel_amount(cand)
    axes = [_product_fit(cand), _volume(cand, est), _access(cand, max_distance_km),
            _repeat(cand), _safety(cand)]
    if weights:
        axes = [Axis(a.key, a.label, a.score, weights.get(a.key, a.weight), a.detail) for a in axes]

    total = sum(a.points for a in axes)
    # 배점을 바꿔도 100점 만점으로 읽히게 정규화한다.
    span = sum(a.weight for a in axes) or 1.0
    total = round(total / span * 100, 1)
    grade = next(g for cut, g in GRADE_CUTS if total >= cut)
    return Fitness(total=total, grade=grade, axes=axes,
                   headline=_headline(cand, est), cautions=_cautions(cand),
                   est_panel_amount=est)
