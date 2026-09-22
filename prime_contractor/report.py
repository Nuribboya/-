"""콘솔 표 / CSV 출력."""
from __future__ import annotations

import csv
import unicodedata
from pathlib import Path

from prime_contractor.pipeline import ScreenResult


def _width(text: str) -> int:
    """한글은 터미널에서 두 칸을 먹는다."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _clip(text: str, width: int) -> str:
    if _width(text) <= width:
        return text
    out = ""
    for ch in text:
        if _width(out + ch) > width - 1:
            return out + "…"
        out += ch
    return out


def render_table(result: ScreenResult, limit: int = 20, show_excluded: bool = True) -> str:
    lines: list[str] = []
    stats = " / ".join(f"{k} {v}" for k, v in result.stats.items())
    lines.append(f"■ 원청 후보 스크리닝 결과  ({stats})")
    for note in result.notes:
        lines.append(f"  - {note}")
    lines.append("")

    cols = [("#", 3), ("업체/기관", 26), ("구분", 6), ("업종", 18),
            ("지역", 8), ("거리", 7), ("수주", 10), ("점수", 6)]
    header = " ".join(_pad(name, w) for name, w in cols)
    lines.append(header)
    lines.append("-" * _width(header))

    for i, c in enumerate(result.passed[:limit], 1):
        dist = f"{c.distance_km:.0f}km" if c.distance_km is not None else "미상"
        awards = f"{c.award_count}건/{c.award_amount / 1e8:.1f}억" if c.award_count else "-"
        row = [str(i), _clip(c.name, 26), "원청" if c.kind == "contractor" else "발주처",
               _clip(c.sector or "미분류", 18), c.region or "미상", dist, awards, f"{c.score:.1f}"]
        lines.append(" ".join(_pad(v, w) for v, (_, w) in zip(row, cols)))

    if show_excluded and result.excluded:
        lines.append("")
        lines.append(f"■ 제외된 후보 ({len(result.excluded)}건) — 기존 원청과 업종이 겹치거나 이력 부족")
        for c in result.excluded[:limit]:
            reason = c.overlap.reasons[0] if c.overlap and c.overlap.reasons else ""
            label = c.overlap.label if c.overlap else ""
            lines.append(f"  · {_clip(c.name, 24)} [{label}] {_clip(reason, 60)}")
    return "\n".join(lines)


CSV_HEADER = [
    "순위", "업체명", "구분", "업종", "점수", "지역", "안성거리km",
    "낙찰건수", "낙찰금액", "사업자번호", "업종코드", "주소", "대표자",
    "겹침판정", "판정근거", "대표공고", "점수상세",
]


def write_csv(result: ScreenResult, path: str | Path, include_excluded: bool = False) -> Path:
    path = Path(path)
    rows = [(i, c, "통과") for i, c in enumerate(result.passed, 1)]
    if include_excluded:
        rows += [(0, c, "제외") for c in result.excluded]

    # 엑셀에서 한글이 깨지지 않도록 BOM 을 붙인다.
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_HEADER + ["상태"])
        for rank, c, status in rows:
            writer.writerow([
                rank or "", c.name,
                "원청후보" if c.kind == "contractor" else "발주처",
                c.sector, c.score, c.region,
                "" if c.distance_km is None else c.distance_km,
                c.award_count, c.award_amount, c.bizno, c.ksic_code, c.address, c.ceo,
                c.overlap.label if c.overlap else "",
                "; ".join(c.overlap.reasons) if c.overlap else "",
                c.awards[0].title if c.awards else "",
                " ".join(f"{k}={v}" for k, v in c.score_breakdown.items()),
                status,
            ])
    return path
