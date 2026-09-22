"""콘솔 표 / CSV 출력."""
from __future__ import annotations

import csv
from pathlib import Path

from prime_contractor.pipeline import ScreenResult
from prime_contractor.textutil import clip as _clip
from prime_contractor.textutil import display_width as _width
from prime_contractor.textutil import pad as _pad


def render_table(result: ScreenResult, limit: int = 20, show_excluded: bool = True) -> str:
    lines: list[str] = []
    stats = " / ".join(f"{k} {v}" for k, v in result.stats.items())
    lines.append(f"■ 일감 줄 만한 회사 찾기 결과  ({stats})")
    for note in result.notes:
        lines.append(f"  - {note}")
    lines.append("")

    cols = [("#", 3), ("등급", 4), ("회사 이름", 26), ("어떤 곳", 7), ("하는 일", 18),
            ("지역", 8), ("안성에서", 8), ("예상 판넬", 10), ("점수", 5)]
    if not result.passed and not result.excluded:
        lines.append("한 곳도 못 찾았습니다. 이렇게 해보세요:")
        lines.append("  1) python -m prime_contractor.cli probe   ← 어디서 막혔는지 알려줍니다")
        lines.append("  2) data.go.kr 마이페이지에서 「낙찰정보서비스」가 '승인' 인지 확인")
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
        lines.append("등급  " + "   ".join(f"{g} {by_grade[g]}곳" for g in "ABCD" if g in by_grade))
        lines.append("  A 먼저 연락 / B 연락해 볼 만함 / C 여유 있을 때 / D 지금은 아님")
        lines.append("'어떤 곳' — 원청: 공사를 따내 판넬을 주문하는 회사 / "
                     "발주처: 공사를 맡기는 관공서")
        lines.append("'예상 판넬' 은 공사비 중 판넬 몫을 어림잡은 값입니다. 실제 견적과 다릅니다.")

    if show_excluded and result.excluded:
        lines.append("")
        lines.append(f"■ 뺀 곳 ({len(result.excluded)}곳) — 왜 뺐는지 뒤에 적어 두었습니다")
        for c in result.excluded[:limit]:
            reason = c.overlap.reasons[0] if c.overlap and c.overlap.reasons else ""
            label = c.overlap.label if c.overlap else ""
            lines.append(f"  · {_clip(c.name, 24)} [{label}] {_clip(reason, 60)}")
    return "\n".join(lines)


CSV_HEADER = [
    "순위", "등급", "점수", "어떻게 할까", "회사 이름", "어떤 곳", "하는 일",
    "예상 판넬 일감(원)", "한 줄 요약",
    "판넬일감 점수", "일감크기 점수", "거리 점수", "꾸준함 점수", "안전 점수",
    "판넬일감 근거", "일감크기 근거", "거리 근거", "꾸준함 근거", "안전 근거",
    "지역", "안성에서(km)", "따낸 공사 수", "공사비 합계(원)",
    "사업자번호", "사업자 상태", "업종코드", "주소", "대표자",
    "케이씨그룹과", "판정 이유", "대표 공사명", "확인하실 점",
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
                c.award_count, c.award_amount, c.bizno,
                c.business_status or "확인 안 함", c.ksic_code, c.address, c.ceo,
                c.overlap.label if c.overlap else "",
                "; ".join(c.overlap.reasons) if c.overlap else "",
                c.awards[0].title if c.awards else "",
                "; ".join(getattr(fit, "cautions", [])),
                status,
            ])
    return path


def render_gap(book, record, plan) -> str:
    """매출 미달 상황과 그걸 메울 후보를 한 화면에 보여 준다."""
    lines = ["■ 매출은 어떤가요"]
    if record is None:
        lines.append("  끝난 달이 없습니다. 매출 파일에 지난달 기록이 들어 있는지 봐주세요.")
        return "\n".join(lines)

    rate = f"{record.rate * 100:.0f}%" if record.rate is not None else "목표 미설정"
    lines.append(f"  {record.ym}   번 돈 {record.revenue / 1e4:,.0f}만원  /  "
                 f"목표 {record.target / 1e4:,.0f}만원   (목표의 {rate})")
    if record.gap:
        lines.append(f"  → {record.gap / 1e4:,.0f}만원 모자랍니다")
    else:
        lines.append("  → 목표를 채우셨습니다")
    if book.source:
        lines.append(f"  출처: {book.source}")

    lines.append("")
    lines.append("■ 이만큼 채우려면 어디에 연락하면 되나")
    lines.append(f"  {plan.note}")
    if not plan.rows:
        return "\n".join(lines)

    cols = [("#", 3), ("등급", 4), ("회사 이름", 26), ("지역", 8),
            ("한 달 예상", 12), ("합치면", 12)]
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
    lines.append("'한 달 예상 금액' = 최근 공사에서 판넬 몫 ÷ 조회 개월수 "
                 "× 연락했을 때 일이 올 확률 (A 35% · B 25% · C 15% · D 8%)")
    lines.append("전부 어림짐작입니다. 어디에 먼저 연락할지 정하는 데만 쓰세요.")
    return "\n".join(lines)
