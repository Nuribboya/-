"""고정비 장부 읽기 — 인건비·임차료처럼 매출과 상관없이 나가는 돈.

지출 장부(expenses.py)는 '1월 | 금액'처럼 달마다 한 칸짜리 표를 여럿 찾아
더한다. 고정비 표는 모양이 두 가지로 흔하다.

1. 달마다 적은 표 — 항목이 옆으로 늘어선다.

       월    인건비      임차료    보험료   (합계)
       1월   18,000,000  3,000,000  900,000
       2월   18,500,000  3,000,000  900,000

   또는 그걸 뒤집어 달이 위로 늘어선 표(항목이 행). 한 달 고정비는 그 달
   줄(또는 열)의 숫자를 다 더한 값이다. '합계' 칸이 따로 있으면 그 칸만 쓴다
   — 다 더하면 합계까지 두 번 세기 때문이다.

2. 달 구분 없는 항목표 — '인건비 18,000,000 / 임차료 3,000,000'. 매달 같은
   돈이 나간다고 보고 항목을 더한다('합계' 줄이 있으면 그 줄).

1번이면 달마다 다른 고정비(상여금 달 등)를 그 달 손익에 그대로 쓰고,
2번이면 '월 고정비' 한 숫자로 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from prime_contractor.expenses import ExpenseBook, ExpenseRecord
from prime_contractor.sales import _guess_year, _month_of

#: '합계' 칸·줄로 보는 이름. 이 칸이 있으면 나머지를 더하지 않고 이것만 쓴다.
_TOTAL_LABEL = re.compile(r"^\s*(월\s*)?(합\s*계|총\s*계|소\s*계|계|total)\s*$", re.IGNORECASE)
#: '평균'처럼 더하면 안 되는 줄·칸.
_SKIP_LABEL = re.compile(r"평\s*균|average", re.IGNORECASE)
#: 월 고정비 대표값을 낼 때 볼 최근 달 수.
_TYPICAL_MONTHS = 3


@dataclass
class FixedCosts:
    by_month: ExpenseBook | None = None                          # 1번 모양
    items: list[tuple[str, int]] = field(default_factory=list)   # 2번 모양
    source: str = ""

    def amount(self, ym: str) -> int:
        """그 달에 적힌 고정비. 달별 표가 아니거나 그 달이 없으면 0."""
        return self.by_month.amount(ym) if self.by_month else 0

    def typical(self) -> int:
        """'월 고정비' 칸에 넣을 대표값 — 최근 몇 달 평균, 또는 항목 합계."""
        if self.by_month and self.by_month.months:
            recent = self.by_month.months[-_TYPICAL_MONTHS:]
            return int(sum(m.amount for m in recent) / len(recent))
        return sum(amount for _, amount in self.items)

    def describe(self) -> str:
        if self.by_month and self.by_month.months:
            return (f"고정비 장부 {len(self.by_month.months)}개월치 "
                    f"(최근 평균 {self.typical() / 1e4:,.0f}만원)")
        return f"고정비 장부 항목 {len(self.items)}개 합계 {self.typical() / 1e4:,.0f}만원"


def load_fixed_costs(path: str | Path) -> FixedCosts:
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("고정비 장부는 엑셀(.xlsx) 파일만 지원합니다.")
    from prime_contractor.xlsx import col_index, read_sheets

    totals: dict[str, int] = {}
    items: list[tuple[str, int]] = []
    for name, grid in read_sheets(path).items():
        monthly = _monthly_table(grid, col_index)
        if monthly:
            for row, pairs in monthly:
                year = _guess_year(grid, name, path, {"rows": {row: None}})
                for month, amount in pairs:
                    ym = f"{year:04d}-{month:02d}"
                    totals[ym] = totals.get(ym, 0) + amount
        else:
            items.extend(_item_list(grid, col_index))

    if totals:
        records = [ExpenseRecord(ym=ym, amount=a) for ym, a in sorted(totals.items()) if a > 0]
        return FixedCosts(by_month=ExpenseBook(months=records, source=str(path)),
                          source=str(path))
    if items:
        return FixedCosts(items=items, source=str(path))
    raise ValueError("엑셀에서 고정비를 찾지 못했습니다. '1월 | 인건비 | 임차료…'처럼 달마다 "
                     "적거나, '인건비 | 18,000,000'처럼 항목과 금액을 나란히 적어 주세요.")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _monthly_table(grid, col_index) -> list[tuple[int, list[tuple[int, int]]]]:
    """달 이름이 세로(한 열)나 가로(한 행)로 두 개 이상 늘어선 표를 찾는다.

    돌려주는 값: [(연도 찾을 기준 행, [(월, 그 달 고정비), ...]), ...] — 표마다 하나.
    """
    labels = [(r, c, m) for (r, c), v in grid.items()
              if isinstance(v, str) and (m := _month_of(v)) is not None]
    by_col: dict[str, list[tuple[int, int]]] = {}
    by_row: dict[int, list[tuple[str, int]]] = {}
    for r, c, m in labels:
        by_col.setdefault(c, []).append((r, m))
        by_row.setdefault(r, []).append((c, m))

    tables = []
    vertical = {col for col, rows in by_col.items() if len(rows) >= 2}
    for col, rows in by_col.items():           # 달이 세로로 — 한 달 = 오른쪽 한 줄
        if col not in vertical:
            continue
        pairs = [(m, _sum_line(grid, col_index, r, col, across=True)) for r, m in sorted(rows)]
        pairs = [(m, a) for m, a in pairs if a > 0]
        if pairs:
            tables.append((min(r for r, _ in rows), pairs))
    for row, cols in by_row.items():           # 달이 가로로 — 한 달 = 아래 한 열
        # 세로 표가 나란히 붙어 있으면(1월이 A열과 D열에) 같은 행에 달이 둘 생긴다.
        # 그건 가로 표가 아니다 — 세로 표로 이미 읽은 열의 달은 빼고 센다.
        cols = [(c, m) for c, m in cols if c not in vertical]
        if len(cols) < 2:
            continue
        pairs = [(m, _sum_line(grid, col_index, row, c, across=False))
                 for c, m in sorted(cols, key=lambda t: col_index(t[0]))]
        pairs = [(m, a) for m, a in pairs if a > 0]
        if pairs:
            tables.append((row, pairs))
    return tables


def _sum_line(grid, col_index, row: int, col: str, across: bool) -> int:
    """달 이름 칸에서 오른쪽(across) 또는 아래로 이어진 숫자를 더한다.

    그 줄에 대응하는 머리글(across 면 위쪽 행, 아니면 왼쪽 열)이 '합계'인 칸이
    있으면 그 칸 하나만 쓴다. '평균' 머리글 칸은 뺀다. 다른 달 이름이 나오면
    거기서 멈춘다 — 옆에 붙은 다른 표까지 더하지 않게.
    """
    start = col_index(col)
    cells = []
    for (r, c), v in grid.items():
        if across and r == row and col_index(c) > start:
            cells.append((col_index(c), c, r, v))
        elif not across and c == col and r > row:
            cells.append((r, c, r, v))
    cells.sort()

    numbers: list[tuple[str, int, float]] = []
    for _, c, r, v in cells:
        if isinstance(v, str) and _month_of(v) is not None:
            break
        if _is_number(v):
            numbers.append((c, r, v))

    total_cell = None
    kept = []
    for c, r, v in numbers:
        head = _header_of(grid, col_index, row, col, c, r, across)
        if head and _TOTAL_LABEL.match(head):
            total_cell = v
        elif head and _SKIP_LABEL.search(head):
            continue
        else:
            kept.append(v)
    if total_cell is not None:
        return int(total_cell)
    return int(sum(kept))


def _header_of(grid, col_index, row: int, col: str, c: str, r: int, across: bool) -> str:
    """숫자 칸의 머리글. across 면 같은 열의 위쪽 글자, 아니면 같은 행의 왼쪽 글자."""
    if across:
        above = [(rr, v) for (rr, cc), v in grid.items()
                 if cc == c and rr < row and isinstance(v, str) and v.strip()]
        return max(above)[1] if above else ""
    left = [(col_index(cc), v) for (rr, cc), v in grid.items()
            if rr == r and col_index(cc) < col_index(col) and isinstance(v, str) and v.strip()]
    return max(left)[1] if left else ""


def _item_list(grid, col_index) -> list[tuple[str, int]]:
    """'인건비 | 18,000,000' 처럼 글자 옆에 금액이 있는 줄. '합계' 줄이 있으면 그것만."""
    rows: dict[int, list[tuple[int, object]]] = {}
    for (r, c), v in grid.items():
        rows.setdefault(r, []).append((col_index(c), v))

    items: list[tuple[str, int]] = []
    total = None
    for r in sorted(rows):
        cells = sorted(rows[r], key=lambda t: t[0])
        label = next((v.strip() for _, v in cells if isinstance(v, str) and v.strip()), "")
        amount = next((v for _, v in cells if _is_number(v)), None)
        if not label or amount is None or amount <= 0:
            continue
        if _TOTAL_LABEL.match(label):
            total = int(amount)
        elif not _SKIP_LABEL.search(label):
            items.append((label, int(amount)))
    if total is not None:
        return [("합계", total)]
    return items
