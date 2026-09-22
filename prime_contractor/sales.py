"""매출 기록과 '부족분을 채울 후보 고르기'.

매출 앱(sales_report)이 쓰는 스키마를 그대로 읽는다.

    {"months":  [{"ym": "2026-09", "revenue": 19320000, "qty": 2476}, ...],
     "targets": {"2026-09": 27000000},
     "catsByMonth": {...}, "meta": {...}}

목표에 못 미친 달이 나오면 그 **부족분만큼** 후보를 골라 준다. 후보를 점수순
으로 나열만 하면 '몇 곳을 접촉해야 메꿔지는지'를 알 수 없기 때문이다.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from prime_contractor.models import Candidate

#: 등급별 수주 확률(어림값). 접촉한 원청 중 실제로 일감이 오는 비율.
#: 실적이 쌓이면 이 숫자를 본인 경험치로 바꾸는 게 맞다.
WIN_RATE = {"A": 0.35, "B": 0.25, "C": 0.15, "D": 0.08}
DEFAULT_WIN_RATE = 0.15


@dataclass
class MonthRecord:
    ym: str                  # "2026-09"
    revenue: int = 0
    target: int = 0
    qty: int = 0

    @property
    def gap(self) -> int:
        """목표 대비 부족분. 목표를 넘겼으면 0."""
        return max(self.target - self.revenue, 0) if self.target else 0

    @property
    def rate(self) -> float | None:
        return self.revenue / self.target if self.target else None

    @property
    def achieved(self) -> bool | None:
        return None if not self.target else self.revenue >= self.target


@dataclass
class SalesBook:
    months: list[MonthRecord] = field(default_factory=list)
    source: str = ""

    def sorted_months(self) -> list[MonthRecord]:
        return sorted(self.months, key=lambda m: m.ym)

    def latest_closed(self, today: date | None = None) -> MonthRecord | None:
        """진행 중인 달은 빼고 가장 최근 달. 진행 중인 달은 당연히 미달로 보인다."""
        today = today or date.today()
        current = f"{today.year:04d}-{today.month:02d}"
        closed = [m for m in self.sorted_months() if m.ym < current]
        return closed[-1] if closed else None

    def month(self, ym: str) -> MonthRecord | None:
        return next((m for m in self.months if m.ym == ym), None)

    def recent_gap(self, months_back: int = 3, today: date | None = None) -> int:
        """최근 몇 달의 부족분 합계. 한 달만 보면 들쑥날쑥해서 오판하기 쉽다."""
        today = today or date.today()
        current = f"{today.year:04d}-{today.month:02d}"
        closed = [m for m in self.sorted_months() if m.ym < current]
        return sum(m.gap for m in closed[-months_back:])


def load_sales(path: str | Path) -> SalesBook:
    """매출 앱의 JSON 스냅샷 또는 CSV 를 읽는다."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        return _from_snapshot(json.loads(text), source=str(path))
    return _from_csv(text, source=str(path))


def _from_snapshot(payload: dict, source: str = "") -> SalesBook:
    targets = {str(k): _to_int(v) for k, v in (payload.get("targets") or {}).items()}
    months = []
    for row in payload.get("months") or []:
        ym = str(row.get("ym") or row.get("month") or "").strip()
        if not ym:
            continue
        months.append(MonthRecord(ym=ym, revenue=_to_int(row.get("revenue")),
                                  target=targets.get(ym, 0), qty=_to_int(row.get("qty"))))
    return SalesBook(months=months, source=source)


def _from_csv(text: str, source: str = "") -> SalesBook:
    """`연월,매출,목표` 형태의 CSV. 열 이름은 한글/영문 둘 다 받는다."""
    rows = list(csv.DictReader(text.splitlines()))
    months = []
    for row in rows:
        lower = { (k or "").strip().lower(): (v or "").strip() for k, v in row.items() }
        ym = _first(lower, ("연월", "월", "ym", "month", "date"))
        if not ym:
            continue
        months.append(MonthRecord(
            ym=_normalize_ym(ym),
            revenue=_to_int(_first(lower, ("매출", "매출액", "revenue", "amount"))),
            target=_to_int(_first(lower, ("목표", "목표액", "target", "goal"))),
        ))
    return SalesBook(months=months, source=source)


# --- 부족분을 채울 후보 고르기 ---------------------------------------------------

@dataclass
class PlanRow:
    candidate: Candidate
    monthly_expected: int      # 이 한 곳에서 기대할 수 있는 월 판넬 매출
    cumulative: int            # 여기까지 누적


@dataclass
class GapPlan:
    gap: int = 0                       # 메워야 할 금액(월)
    rows: list[PlanRow] = field(default_factory=list)
    covered: int = 0
    lookback_days: int = 180
    note: str = ""

    @property
    def is_covered(self) -> bool:
        return self.gap > 0 and self.covered >= self.gap

    @property
    def shortfall_left(self) -> int:
        return max(self.gap - self.covered, 0)


def monthly_expected(cand: Candidate, lookback_days: int,
                     win_rate: dict[str, float] | None = None) -> int:
    """이 후보 한 곳에서 기대할 수 있는 **월** 판넬 매출.

    추정 판넬 물량은 조회 기간 전체의 값이라 월로 나눠야 하고, 접촉한다고 다
    수주하는 것도 아니라 등급별 확률을 곱한다. 어림값이다.
    """
    est = getattr(cand.fitness, "est_panel_amount", 0)
    if not est or lookback_days <= 0:
        return 0
    per_month = est / (lookback_days / 30.0)
    rate = (win_rate or WIN_RATE).get(cand.grade, DEFAULT_WIN_RATE)
    return int(per_month * rate)


def plan_to_close_gap(gap: int, candidates: list[Candidate], lookback_days: int = 180,
                      max_rows: int = 12, win_rate: dict[str, float] | None = None) -> GapPlan:
    """부족분을 메울 만큼 후보를 위에서부터 담는다.

    적합도 순으로 담되, 기대 매출이 0 인 곳은 아무리 점수가 높아도 부족분을
    메우는 데 도움이 안 되므로 뺀다.
    """
    plan = GapPlan(gap=max(gap, 0), lookback_days=lookback_days)
    if plan.gap <= 0:
        plan.note = "목표를 채웠습니다. 부족분이 없습니다."
        return plan

    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    total = 0
    for cand in ranked:
        expected = monthly_expected(cand, lookback_days, win_rate)
        if expected <= 0:
            continue
        total += expected
        plan.rows.append(PlanRow(candidate=cand, monthly_expected=expected, cumulative=total))
        if total >= plan.gap or len(plan.rows) >= max_rows:
            break

    plan.covered = total
    if plan.is_covered:
        plan.note = (f"{len(plan.rows)}곳을 접촉하면 월 {total / 1e4:,.0f}만원이 기대됩니다 "
                     f"(부족분 {plan.gap / 1e4:,.0f}만원).")
    else:
        plan.note = (f"후보 {len(plan.rows)}곳을 다 합쳐도 월 {total / 1e4:,.0f}만원으로 "
                     f"{plan.shortfall_left / 1e4:,.0f}만원이 모자랍니다. "
                     f"조회 기간·반경을 넓히거나 업종을 더 열어 보세요.")
    return plan


# --- 잡다한 변환 ---------------------------------------------------------------

def _first(row: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        if row.get(k):
            return row[k]
    return ""


def _normalize_ym(text: str) -> str:
    """'2026/9', '2026.09', '2026-09-30' → '2026-09'."""
    cleaned = text.replace("/", "-").replace(".", "-").strip()
    parts = [p for p in cleaned.split("-") if p]
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}"
    return cleaned


def _to_int(value) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    digits = "".join(c for c in str(value) if c.isdigit() or c == "-")
    return int(digits) if digits.lstrip("-") else 0
