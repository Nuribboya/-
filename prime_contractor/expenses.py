"""월별 지출 장부 읽기 — 매출 장부와 같은 방식으로 실제 지출을 읽는다.

손익분기(breakeven.py)는 '월 고정비 얼마, 재료비 몇 %'라는 어림값으로 그 달
손익을 추정한다. 실제 지출 장부가 있으면 어림값 대신 그 달 실제 지출을 그대로
써서 '매출 − 실제 지출'로 정확한 손익을 계산할 수 있다.

엑셀 읽기는 sales.py 의 블록 찾기 로직을 그대로 재사용한다 — '1월' 옆 칸의
금액을 찾는 방식은 매출이든 지출이든 똑같기 때문이다.

매출 장부는 '우리 매출' 블록 하나만 고르면 되지만(같은 매출이 거래처 쪽
표에도 또 나오면 두 번 세게 된다), 지출 장부는 반대다 — 식대비·전기요금·
부가가치세처럼 서로 다른 항목이 블록별로 나뉘어 있는 게 보통이고, 그
항목들은 다 더해야 그 달 총지출이 된다. 그래서 가장 그럴듯한 블록 하나만
쓰지 않고, 찾은 블록을 전부 월별로 합산한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from prime_contractor.sales import _block_to_months, _guess_year, _month_blocks


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
    """지출 장부(.xlsx)를 읽는다. '1월' 같은 월 이름과 그 옆 칸의 금액을 찾아 다 더한다."""
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("지출 장부는 엑셀(.xlsx) 파일만 지원합니다.")
    from prime_contractor.xlsx import col_index, read_sheets

    sheets = read_sheets(path)
    totals: dict[str, int] = {}
    found_any = False
    for name, grid in sheets.items():
        blocks = _month_blocks(grid, col_index)
        if not blocks:
            continue
        for block in blocks:
            year = _guess_year(grid, name, path, block)
            months = _block_to_months(block, year)
            if not months:
                continue
            found_any = True
            for m in months:
                totals[m.ym] = totals.get(m.ym, 0) + m.revenue

    if not found_any:
        raise ValueError("엑셀에서 월별 지출을 찾지 못했습니다. "
                         "'1월' 같은 월 이름과 그 옆 칸에 금액이 있어야 합니다.")
    records = [ExpenseRecord(ym=ym, amount=amount) for ym, amount in sorted(totals.items())]
    return ExpenseBook(months=records, source=str(path))
