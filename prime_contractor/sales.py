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
import re
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

    def apply_target(self, monthly: int, overwrite: bool = False) -> int:
        """목표가 비어 있는 달에 월 목표를 채운다. 채운 달 수를 돌려준다.

        손으로 쓰는 장부에는 목표 칸이 없는 경우가 많다. 그렇다고 목표 없이
        두면 '얼마나 모자라는지'를 셀 수 없어 부족분 채우기가 동작하지 않는다.
        """
        if monthly <= 0:
            return 0
        filled = 0
        for m in self.months:
            if overwrite or not m.target:
                m.target = monthly
                filled += 1
        return filled

    @property
    def has_targets(self) -> bool:
        return any(m.target for m in self.months)

    def average_revenue(self, months_back: int = 6, today: date | None = None) -> int:
        """최근 몇 달 평균 매출. 목표를 정할 때 기준으로 삼기 좋다."""
        today = today or date.today()
        current = f"{today.year:04d}-{today.month:02d}"
        closed = [m for m in self.sorted_months() if m.ym < current and m.revenue]
        recent = closed[-months_back:]
        return int(sum(m.revenue for m in recent) / len(recent)) if recent else 0

    def recent_gap(self, months_back: int = 3, today: date | None = None) -> int:
        """최근 몇 달의 부족분 합계. 한 달만 보면 들쑥날쑥해서 오판하기 쉽다."""
        today = today or date.today()
        current = f"{today.year:04d}-{today.month:02d}"
        closed = [m for m in self.sorted_months() if m.ym < current]
        return sum(m.gap for m in closed[-months_back:])


def load_sales(path: str | Path) -> SalesBook:
    """매출 앱의 JSON 스냅샷, 엑셀 장부(.xlsx), 또는 CSV 를 읽는다."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return _from_xlsx(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        return _from_snapshot(json.loads(text), source=str(path))
    return _from_csv(text, source=str(path))


# --- 엑셀 장부 읽기 -------------------------------------------------------------
#
# 손으로 만든 장부는 표가 반듯하지 않다. 실제 파일에서 본 것들:
#   · 한 행에 블록이 좌우로 둘 (왼쪽은 우리 매출, 오른쪽은 거래처 마감)
#   · 합계가 '10월' 자리에 들어앉아 있다 (1~9월 합계가 10월 칸에)
#   · 연도는 칸이 아니라 제목 글자 안에 있다 ("2026년 월매출원장리스트")
# 그래서 '월 이름 옆의 숫자'를 블록별로 모으고, 앞선 값들의 합과 같은 칸은
# 합계로 보고 버린다. 합계를 매출로 잘못 읽으면 목표 대비 계산이 통째로 어긋난다.

_MONTH_LABEL = re.compile(r"^\s*(1[0-2]|[1-9])\s*월\s*$")
#: "7월25일" 처럼 날짜까지 박힌 라벨 — 부가가치세 납부일정 같은 장부에서 흔하다.
_MONTH_DAY_LABEL = re.compile(r"^\s*(1[0-2]|[1-9])\s*월\s*\d{1,2}\s*일\s*$")
_YM_LABEL = re.compile(r"^\s*(20\d{2})\s*[-./년]\s*(1[0-2]|0?[1-9])\s*월?\s*$")
_YEAR_IN_TEXT = re.compile(r"(20\d{2})\s*년")

#: 월 이름에서 오른쪽으로 이만큼 안에 있는 숫자를 그 달의 금액으로 본다.
_AMOUNT_SEARCH_WIDTH = 4
#: 이 글자가 머리글에 있으면 '우리 매출' 블록으로 본다.
_REVENUE_HINTS = ("매출", "수입", "판매")
_TARGET_HINTS = ("목표", "계획")


def _from_xlsx(path: Path) -> SalesBook:
    from prime_contractor.xlsx import col_index, read_sheets

    sheets = read_sheets(path)
    best: tuple[int, list[MonthRecord]] = (-1, [])
    for name, grid in sheets.items():
        blocks = _month_blocks(grid, col_index)
        if not blocks:
            continue
        for block in blocks:
            year = _guess_year(grid, name, path, block)
            months = _block_to_months(block, year)
            if not months:
                continue
            rank = _block_rank(name, block, len(months))
            if rank > best[0]:
                best = (rank, months)

    if not best[1]:
        raise ValueError("엑셀에서 월별 매출을 찾지 못했습니다. "
                         "'1월' 같은 월 이름과 그 옆 칸에 금액이 있어야 합니다.")
    return SalesBook(months=best[1], source=str(path))


#: 같은 열에서 행이 이보다 더 벌어지면 다른 표로 본다 — 표 사이엔 보통
#: 제목·머리글·빈 줄이 몇 줄씩 낀다. 한 표 안에서 몇 달치가 통째로 비는
#: 경우는 흔치 않다.
_MAX_ROW_GAP = 3


def _month_blocks(grid, col_index) -> list[dict]:
    """월 이름 + 오른쪽 금액을 찾아, 금액이 놓인 열 기준으로 묶는다.

    같은 행에 블록이 여럿이어도 열이 다르므로 자연히 나뉜다. 반대로 세로로
    쌓인 서로 다른 표가 같은 열에 금액을 두면(식대비 표 밑에 부가세 표가
    또 있는 식) 열만 보고 묶으면 안 된다 — 그러면 서로 다른 표의 같은 달이
    '중복'으로 처리돼 뒤엣것이 조용히 버려진다. 그래서 열이 같아도 행이
    많이 벌어지면 별개 블록으로 쪼갠다.
    """
    raw: dict[str, dict[int, tuple[int, float]]] = {}
    for (row, col), value in grid.items():
        if not isinstance(value, str):
            continue
        month = _month_of(value)
        if month is None:
            continue
        found = _amount_right_of(grid, row, col, col_index)
        if found is None:
            continue
        amount_col, amount = found
        raw.setdefault(amount_col, {})[row] = (month, amount)

    blocks: list[dict] = []
    for amount_col, rows in raw.items():
        blocks.extend(_split_by_row_gap(amount_col, rows))
    return blocks


def _split_by_row_gap(col: str, rows: dict[int, tuple[int, float]]) -> list[dict]:
    ordered = sorted(rows)
    groups: list[list[int]] = []
    for row in ordered:
        if groups and row - groups[-1][-1] <= _MAX_ROW_GAP:
            groups[-1].append(row)
        else:
            groups.append([row])
    return [{"col": col, "rows": {r: rows[r] for r in group}} for group in groups]


def _month_of(text: str) -> int | None:
    ym = _YM_LABEL.match(text)
    if ym:
        return int(ym.group(2))
    plain = _MONTH_LABEL.match(text)
    if plain:
        return int(plain.group(1))
    day = _MONTH_DAY_LABEL.match(text)
    return int(day.group(1)) if day else None


def _amount_right_of(grid, row: int, col: str, col_index):
    """월 이름 오른쪽에서 가장 가까운 숫자 칸."""
    start = col_index(col)
    for (r, c), v in grid.items():
        if r != row or not isinstance(v, (int, float)):
            continue
        gap = col_index(c) - start
        if 0 < gap <= _AMOUNT_SEARCH_WIDTH:
            return c, float(v)
    return None


#: 합계로 보려면 앞에 최소 이만큼의 달이 있어야 한다.
_MIN_MONTHS_BEFORE_TOTAL = 2


def _block_to_months(block: dict, year: int) -> list[MonthRecord]:
    """행 순서대로 읽고, 맨 끝에 붙은 합계 칸만 걷어낸다.

    '앞선 값들의 합과 같으면 합계'라는 규칙을 아무 칸에나 적용하면 안 된다.
    3월 매출이 우연히 1·2월 합과 같으면 3월이 통째로 사라진다. 합계는 표
    맨 아래에 붙으므로 **마지막 값만** 검사한다.
    """
    entries: list[tuple[str, float]] = []
    seen: set[str] = set()
    for row in sorted(block["rows"]):
        month, amount = block["rows"][row]
        if amount <= 0:
            continue
        ym = f"{year:04d}-{month:02d}"
        if ym in seen:                   # 같은 달이 또 나오면 앞엣것만 쓴다
            continue
        seen.add(ym)
        entries.append((ym, amount))

    # 딱 한 번만 걷어낸다. 합계를 뗀 뒤 남은 마지막 달이 또 우연히 나머지의
    # 합과 같을 수 있는데, 거기까지 지우면 멀쩡한 달을 잃는다.
    if len(entries) > _MIN_MONTHS_BEFORE_TOTAL:
        head_sum = sum(a for _, a in entries[:-1])
        if abs(entries[-1][1] - head_sum) < 1.0:
            entries.pop()

    return [MonthRecord(ym=ym, revenue=int(a)) for ym, a in entries]


def _block_rank(sheet_name: str, block: dict, month_count: int) -> int:
    """어느 블록이 '우리 월매출'인지 고른다. 머리글 글자를 우선으로 본다."""
    score = month_count
    if any(h in sheet_name for h in _REVENUE_HINTS):
        score += 20
    if "거래처" in sheet_name or "부가" in sheet_name:
        score -= 15
    return score


#: 블록 바로 위 몇 줄까지를 '그 블록의 제목'으로 본다.
_YEAR_SEARCH_ROWS = 8


def _guess_year(grid, sheet_name: str, path: Path, block: dict | None = None) -> int:
    """연도는 칸이 아니라 제목이나 파일 이름에 있는 경우가 많다.

    한 시트에 블록이 여럿이면(지출 장부에서 흔하다) 블록마다 연도가 다를 수
    있다 — "2025년 부가가치세" 옆에 "2026년 부가가치세" 블록이 나란히 있는
    식으로. 그래서 시트 전체를 무작정 뒤지지 않고, **그 블록 바로 위**에서
    먼저 찾는다. 거기 없으면 시트 이름·파일 이름을 보고, 그래도 없으면
    오늘 연도로 본다 — 엉뚱한 다른 블록의 제목에서 연도를 잘못 가져오는
    것보다는 안전하다.
    """
    if block is not None and block.get("rows"):
        first_row = min(block["rows"])
        nearby = sorted(
            ((first_row - r, v) for (r, c), v in grid.items()
             if isinstance(v, str) and 0 < first_row - r <= _YEAR_SEARCH_ROWS),
            key=lambda t: t[0])
        for _, text in nearby:
            found = _YEAR_IN_TEXT.search(text)
            if found:
                return int(found.group(1))

    for source in (sheet_name, path.name):
        found = _YEAR_IN_TEXT.search(source) or re.search(r"(20\d{2})", source)
        if found:
            return int(found.group(1))
    return date.today().year


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
        plan.note = "목표를 채우셨습니다. 더 찾을 필요가 없습니다."
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
        plan.note = (f"아래 {len(plan.rows)}곳에 연락하면 한 달에 약 {total / 1e4:,.0f}만원이 "
                     f"기대됩니다. 모자란 {plan.gap / 1e4:,.0f}만원은 채울 수 있습니다.")
    else:
        plan.note = (f"찾은 {len(plan.rows)}곳을 다 합쳐도 한 달 약 {total / 1e4:,.0f}만원이라, "
                     f"{plan.shortfall_left / 1e4:,.0f}만원이 여전히 모자랍니다. "
                     f"'최근 며칠치'를 늘리거나 '안성에서 얼마나'를 넓혀서 다시 찾아 보세요.")
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
