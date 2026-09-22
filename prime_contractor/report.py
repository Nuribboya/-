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

    cols = [("#", 3), ("등급", 4), ("업체/기관", 26), ("구분", 6), ("업종", 18),
            ("지역", 8), ("거리", 7), ("추정판넬", 10), ("적합도", 6)]
    if not result.passed and not result.excluded:
        lines.append("후보가 한 곳도 잡히지 않았습니다. 확인할 것:")
        lines.append("  1) python -m prime_contractor.cli probe   ← 어디서 막혔는지 바로 나옵니다")
        lines.append("  2) 공공데이터포털에서 「낙찰정보서비스」 활용신청이 '승인' 상태인지")
        lines.append("  3) --days 365 로 기간을 넓혀보기")
        return "\n".join(lines)

    header = " ".join(_pad(name, w) for name, w in cols)
    lines.append(header)
    lines.append("-" * _width(header))

    for i, c in enumerate(result.passed[:limit], 1):
        dist = f"{c.distance_km:.0f}km" if c.distance_km is not None else "미상"
        est = getattr(c.fitness, "est_panel_amount", 0)
        panel = f"{est / 1e8:.1f}억" if est else "-"
        row = [str(i), c.grade or "-", _clip(c.name, 26),
               "원청" if c.kind == "contractor" else "발주처",
               _clip(c.sector or "미분류", 18), c.region or "미상", dist, panel, f"{c.score:.1f}"]
        lines.append(" ".join(_pad(v, w) for v, (_, w) in zip(row, cols)))

    by_grade: dict[str, int] = {}
    for c in result.passed:
        by_grade[c.grade] = by_grade.get(c.grade, 0) + 1
    if by_grade:
        lines.append("")
        lines.append("등급: " + "  ".join(f"{g} {by_grade[g]}곳" for g in "ABCD" if g in by_grade)
                     + "   (A 우선 접촉 / B 접촉 가치 있음 / C 여력 될 때 / D 보류)")
        lines.append("'추정판넬'은 낙찰금액에 공종별 판넬 비중을 곱한 어림값입니다 — 실제 견적과 다릅니다.")

    if show_excluded and result.excluded:
        lines.append("")
        lines.append(f"■ 제외된 후보 ({len(result.excluded)}건) — 사유는 뒤에 적어 두었습니다")
        for c in result.excluded[:limit]:
            reason = c.overlap.reasons[0] if c.overlap and c.overlap.reasons else ""
            label = c.overlap.label if c.overlap else ""
            lines.append(f"  · {_clip(c.name, 24)} [{label}] {_clip(reason, 60)}")
    return "\n".join(lines)


CSV_HEADER = [
    "순위", "등급", "적합도", "추천", "업체명", "구분", "업종",
    "추정판넬금액", "한줄근거",
    "품목점수", "물량점수", "접근성점수", "지속성점수", "안전도점수",
    "품목근거", "물량근거", "접근성근거", "지속성근거", "안전도근거",
    "지역", "안성거리km", "낙찰건수", "낙찰금액", "사업자번호", "업종코드",
    "주소", "대표자", "겹침판정", "판정근거", "대표공고", "주의사항",
]


def _axis_points(fit, key: str):
    axis = fit.axis(key) if fit else None
    return axis.points if axis else ""


def _axis_detail(fit, key: str) -> str:
    axis = fit.axis(key) if fit else None
    return axis.detail if axis else ""


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
            fit = c.fitness
            writer.writerow([
                rank or "", c.grade, c.score,
                getattr(fit, "advice", ""),
                c.name,
                "원청후보" if c.kind == "contractor" else "발주처",
                c.sector,
                getattr(fit, "est_panel_amount", 0),
                getattr(fit, "headline", ""),
                _axis_points(fit, "product_fit"), _axis_points(fit, "volume"),
                _axis_points(fit, "access"), _axis_points(fit, "repeat"),
                _axis_points(fit, "safety"),
                _axis_detail(fit, "product_fit"), _axis_detail(fit, "volume"),
                _axis_detail(fit, "access"), _axis_detail(fit, "repeat"),
                _axis_detail(fit, "safety"),
                c.region, "" if c.distance_km is None else c.distance_km,
                c.award_count, c.award_amount, c.bizno, c.ksic_code, c.address, c.ceo,
                c.overlap.label if c.overlap else "",
                "; ".join(c.overlap.reasons) if c.overlap else "",
                c.awards[0].title if c.awards else "",
                "; ".join(getattr(fit, "cautions", [])),
                status,
            ])
    return path


def render_gap(book, record, plan) -> str:
    """매출 미달 상황과 그걸 메울 후보를 한 화면에 보여 준다."""
    lines = ["■ 매출 현황"]
    if record is None:
        lines.append("  마감된 달이 없습니다. 매출 데이터를 확인해 주세요.")
        return "\n".join(lines)

    rate = f"{record.rate * 100:.0f}%" if record.rate is not None else "목표 미설정"
    lines.append(f"  {record.ym}  실적 {record.revenue / 1e4:,.0f}만원 / "
                 f"목표 {record.target / 1e4:,.0f}만원  ({rate})")
    if record.gap:
        lines.append(f"  → {record.gap / 1e4:,.0f}만원 부족")
    else:
        lines.append("  → 목표 달성")
    if book.source:
        lines.append(f"  출처: {book.source}")

    lines.append("")
    lines.append("■ 부족분을 채울 후보")
    lines.append(f"  {plan.note}")
    if not plan.rows:
        return "\n".join(lines)

    cols = [("#", 3), ("등급", 4), ("업체/기관", 26), ("지역", 8),
            ("기대 월매출", 12), ("누적", 12)]
    header = " ".join(_pad(n, w) for n, w in cols)
    lines.append("")
    lines.append(header)
    lines.append("-" * _width(header))
    for i, row in enumerate(plan.rows, 1):
        c = row.candidate
        mark = "✔" if row.cumulative >= plan.gap else " "
        values = [str(i), c.grade or "-", _clip(c.name, 26), c.region or "미상",
                  f"{row.monthly_expected / 1e4:,.0f}만원",
                  f"{row.cumulative / 1e4:,.0f}만원 {mark}"]
        lines.append(" ".join(_pad(v, w) for v, (_, w) in zip(values, cols)))

    lines.append("")
    lines.append("'기대 월매출' = 추정 판넬 물량 ÷ 조회월수 × 등급별 수주확률"
                 " (A 35% / B 25% / C 15% / D 8%).")
    lines.append("전부 어림값입니다. 접촉 우선순위를 정하는 용도로만 쓰세요.")
    return "\n".join(lines)
