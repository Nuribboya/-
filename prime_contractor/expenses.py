"""월별 지출 장부 읽기 — 매출 장부와 같은 방식으로 실제 지출을 읽는다.

손익분기(breakeven.py)는 '월 고정비 얼마, 재료비 몇 %'라는 어림값으로 그 달
손익을 추정한다. 실제 지출 장부가 있으면 어림값 대신 그 달 실제 지출을 그대로
써서 '매출 − 실제 지출'로 정확한 손익을 계산할 수 있다.

엑셀 읽기는 sales.py 의 블록 찾기 로직을 그대로 재사용한다 — '1월' 옆 칸의
금액을 찾는 방식은 매출이든 지출이든 똑같기 때문이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from prime_contractor.sales import _block_to_months, _guess_year, _month_blocks

#: 이 글자가 시트 이름에 있으면 '지출' 블록으로 더 쳐준다.
_EXPENSE_HINTS = ("지출", "비용", "요금", "지급")


@dataclass
class ExpenseRecord:
    ym: str                  # "2026-09"
    amount: int = 0


@dataclass
class ExpenseBook:
    months: list[ExpenseRecord] = field(default_factory=list)
    source: str = ""

    def amount(self, ym: str) -> int:
        rec = next((m for m in self.months if m.ym == ym), None)
        return rec.amount if rec else 0


def load_expenses(path: str | Path) -> ExpenseBook:
    """지출 장부(.xlsx)를 읽는다. '1월' 같은 월 이름과 그 옆 칸의 금액을 찾는다."""
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("지출 장부는 엑셀(.xlsx) 파일만 지원합니다.")
    from prime_contractor.xlsx import col_index, read_sheets

    sheets = read_sheets(path)
    best: tuple[int, list] = (-1, [])
    for name, grid in sheets.items():
        blocks = _month_blocks(grid, col_index)
        if not blocks:
            continue
        year = _guess_year(grid, name, path)
        for block in blocks:
            months = _block_to_months(block, year)
            if not months:
                continue
            rank = len(months) + (20 if any(h in name for h in _EXPENSE_HINTS) else 0)
            if rank > best[0]:
                best = (rank, months)

    if not best[1]:
        raise ValueError("엑셀에서 월별 지출을 찾지 못했습니다. "
                         "'1월' 같은 월 이름과 그 옆 칸에 금액이 있어야 합니다.")
    records = [ExpenseRecord(ym=m.ym, amount=m.revenue) for m in best[1]]
    return ExpenseBook(months=records, source=str(path))
