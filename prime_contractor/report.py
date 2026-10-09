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
            ("지역", 8), ("안성에서", 8), ("한 달 판넬", 10), ("점수", 5)]
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
        month = getattr(c.fitness, "monthly_panel_amount", 0)
        panel = (f"{month / 1e8:.1f}억" if month >= 1e8 else f"{month / 1e4:,.0f}만") if month else "-"
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
        lines.append("'한 달 판넬' 은 공사비 중 판넬 몫을 어림잡아 한 달치로 나눈 값입니다. "
                     "실제 견적과 다릅니다.")

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


def _size_or(fit, key: str):
    """'일감 크기' 칸은 우리 월매출을 알면 '규모 맞음'(scale) 축이 대신 들어간다."""
    axis = fit.axis(key) if fit else None
    if axis is None and key == "volume" and fit:
        axis = fit.axis("scale")
    return axis


def _axis_points(fit, key: str):
    axis = _size_or(fit, key)
    return axis.points if axis else ""


def _axis_detail(fit, key: str) -> str:
    axis = _size_or(fit, key)
    return axis.detail if axis else ""


def write_csv(result: ScreenResult, path: str | Path, include_excluded: bool = False,
              min_score: float | None = None) -> Path:
    """후보 목록을 엑셀에서 열 수 있는 CSV 로 쓴다.

    min_score 를 주면 그 점수를 **넘는** 곳만 쓴다(같은 점수는 뺀다). 순위 번호는
    전체 순위를 그대로 둬서, 화면 표와 엑셀의 '#'이 같은 회사를 가리킨다.
    """
    path = Path(path)
    rows = [(i, c, "통과") for i, c in enumerate(result.passed, 1)
            if min_score is None or c.score > min_score]
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


def render_breakeven(book, model, today=None) -> str:
    """달마다 손익분기선 위인지 아래인지, 그래서 얼마 남았는지/잃었는지."""
    from datetime import date
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"

    lines = ["■ 손익분기 기준",
             f"  월 고정비 {model.monthly_fixed / 1e4:,.0f}만원 · "
             f"재료·외주비 {model.variable_ratio * 100:.0f}% "
             f"→ 매출 1만원 중 {model.margin_ratio * 1e4:,.0f}원이 고정비를 갚습니다",
             f"  손익분기 매출  월 {model.breakeven / 1e4:,.0f}만원   ← 이 밑이면 적자"]
    if model.monthly_profit:
        lines.append(f"  목표 매출      월 {model.target / 1e4:,.0f}만원   "
                     f"← 이익 {model.monthly_profit / 1e4:,.0f}만원까지")
    lines.append("")

    cols = [("연월", 9), ("매출", 11), ("손익분기 대비", 14), ("예상 손익", 12), ("", 6)]
    header = " ".join(_pad(n, w) for n, w in cols)
    lines += [header, "-" * _width(header)]
    losing = total = 0
    for m in book.sorted_months():
        running = m.ym >= current
        profit = model.profit_at(m.revenue)
        diff = m.revenue - model.breakeven
        if not running:
            total += profit
            losing += profit < 0
        mark = "진행중" if running else ("적자" if profit < 0 else "")
        row = [m.ym, f"{m.revenue / 1e4:,.0f}만", f"{diff / 1e4:+,.0f}만",
               f"{profit / 1e4:+,.0f}만", mark]
        lines.append(" ".join(_pad(v, w) for v, (_, w) in zip(row, cols)))

    closed = sum(1 for m in book.months if m.ym < current)
    lines.append("")
    lines.append(f"  끝난 {closed}개월 중 적자 {losing}개월 · 누적 손익 {total / 1e4:+,.0f}만원")
    lines.append("  고정비·비율을 대략으로 넣으셨다면 손익도 대략입니다. "
                 "적자/흑자 방향을 보는 데 쓰세요.")
    return "\n".join(lines)


def write_xlsx(result: ScreenResult, path: str | Path, min_score: float | None = None) -> Path:
    """연락할 곳을 인쇄해서 보기 좋은 엑셀로 쓴다.

    CSV 는 칸이 30개 넘게 늘어서 있고 너비·서식이 없어 보기 힘들었고, 화면용으로
    넓게 잡은 표는 인쇄하면 한 회사가 A4 서너 장에 나뉘었다. 시트마다 A4 가로 한 장
    폭에 들어가도록 칸을 나눴다.

      연락할 곳 ... 연락할 때 보는 것만 (순위·등급·점수·회사·지역·거리·일감·이유·대표자·주소)
      추가 정보 ... 대표 공사·사업자번호·상태·확인하실 점
      점수 근거 ... 다섯 항목 점수와 이유
      근처 공장 ... 근처 대기업·중견 공장, 경기 덜 타는 업종 먼저(있을 때만)
      관공서 판넬 시장 ... 관공서가 판넬을 물품으로 산 계약(있을 때만)
      읽는 법 ..... 등급·점수 뜻, 몇 곳 중 몇 곳을 담았나

    min_score 를 주면 그 점수를 넘는 곳만 넣는다. 순위는 화면 표와 같은 번호다.
    """
    from datetime import date

    from prime_contractor.xlsx_writer import (
        CENTER, DECIMAL, GRADE_STYLE, MONEY, TEXT, WRAP, Column, Sheet, write_workbook)

    picked = [(i, c) for i, c in enumerate(result.passed, 1)
              if min_score is None or c.score > min_score]

    # 칸 너비 합을 150 안팎으로 — A4 가로 한 장에 글씨를 줄이지 않고 들어가는 폭.
    main = Sheet("연락할 곳", [
        Column("순위", 5, CENTER), Column("등급", 5, CENTER), Column("점수", 6, DECIMAL),
        Column("회사 이름", 22, WRAP), Column("지역", 7, CENTER), Column("거리(km)", 7, DECIMAL),
        Column("한 달 판넬(원, 어림)", 13, MONEY), Column("왜 이 회사인가", 44, WRAP),
        Column("대표자", 9, CENTER), Column("주소", 30, WRAP),
    ])
    extra = Sheet("추가 정보", [
        Column("순위", 5, CENTER), Column("회사 이름", 22, WRAP), Column("따낸 공사(건)", 8, CENTER),
        Column("대표 공사", 40, WRAP), Column("사업자번호", 12, CENTER),
        Column("사업자 상태", 10, CENTER), Column("확인하실 점", 50, WRAP),
    ])
    detail = Sheet("점수 근거", [
        Column("순위", 5, CENTER), Column("회사 이름", 22, WRAP), Column("점수", 6, DECIMAL),
        Column("판넬 일감 (30)", 30, WRAP), Column("일감 크기 (25)", 26, WRAP),
        Column("거리 (20)", 16, WRAP), Column("꾸준함 (15)", 18, WRAP),
        Column("안전 (10)", 24, WRAP),
    ])
    for row, (rank, c) in enumerate(picked):
        fit = c.fitness
        main.rows.append([
            rank, c.grade, round(c.score, 1), c.name, c.region,
            c.distance_km if c.distance_km is not None else "미상",
            getattr(fit, "monthly_panel_amount", 0) or "",
            getattr(fit, "headline", ""), c.ceo, c.address,
        ])
        main.cell_styles[(row, 1)] = GRADE_STYLE.get(c.grade, CENTER)
        extra.rows.append([
            rank, c.name, c.award_count, c.awards[0].title if c.awards else "",
            c.bizno, c.business_status or "확인 안 함",
            "\n".join(getattr(fit, "cautions", [])),
        ])
        size = _axis(fit, "scale") or _axis(fit, "volume")
        values = [rank, c.name, round(c.score, 1)]
        for axis in (_axis(fit, "product_fit"), size, _axis(fit, "access"),
                     _axis(fit, "repeat"), _axis(fit, "safety")):
            values.append(f"{axis.points:g}점 — {axis.detail}" if axis else "")
        detail.rows.append(values)

    market = Sheet("관공서 판넬 시장", [
        Column("날짜", 11, CENTER), Column("사는 기관", 24, WRAP),
        Column("무엇을 샀나 (공고명)", 56, WRAP), Column("납품한 업체", 22, WRAP),
        Column("금액(원)", 14, MONEY),
    ], rows=[[(a.opening_dt or "")[:10], a.demand_org, a.title, a.winner_name, a.amount]
             for a in getattr(result, "market", [])])

    from prime_contractor.config import load_config
    from prime_contractor.pipeline import steady_of
    cfg = load_config()
    factories = Sheet("근처 공장", [
        Column("순위", 5, CENTER), Column("회사 이름", 22, WRAP), Column("업종", 14, WRAP),
        Column("경기", 7, CENTER), Column("지역", 7, CENTER), Column("거리(km)", 7, DECIMAL),
        Column("대표자", 9, CENTER), Column("주소", 34, WRAP), Column("홈페이지", 22, WRAP),
    ], rows=[[i, c.name, c.sector or "미분류", _steady_text(steady_of(c, cfg)), c.region,
              c.distance_km, c.ceo, c.address, c.homepage]
             for i, c in enumerate(getattr(result, "factories", []), 1)])

    rule = f"{min_score:g}점을 넘는 곳만" if min_score is not None else "전부"
    guide = Sheet("읽는 법", [Column("항목", 18, TEXT), Column("설명", 80, WRAP)], rows=[
        ["만든 날", date.today().isoformat()],
        ["담은 곳", f"찾은 {len(result.passed)}곳 중 {rule} — {len(picked)}곳"],
        ["순위", "프로그램 화면 표의 순위 번호와 같습니다. 시트끼리도 같은 번호입니다."],
        ["A등급 (75점~)", "먼저 연락해 보세요. (초록)"],
        ["B등급 (60~74점)", "연락해 볼 만합니다. (파랑) '점수 근거' 시트에서 판넬 일감에 "
                          "배전반·제어반이 직접 적힌 일이 있는지 보세요."],
        ["점수 (100점 만점)", "판넬 일감 30 · 일감 크기 25 · 거리 20 · 꾸준함 15 · 안전 10"],
        ["한 달 판넬", "조회 기간에 따낸 공사비 중 판넬 몫을 업계 통념으로 어림해 한 달치로 나눈 "
                     "값입니다. 그 회사 전체 물량이라 우리가 다 받는 게 아니고, 견적도 아닙니다."],
        ["거리 '미상'", "주소를 못 찾은 곳입니다. 연락 전에 위치를 확인하세요."],
        ["근처 공장", "안성 근처 대기업·중견 공장(상장사)입니다. 공사 이력이 없어 점수 대신 "
                   "경기를 덜 타는 업종 먼저, 가까운 순으로 담았습니다. 단가는 세지만 협력업체 "
                   "등록이 까다로워, 그 공장 시설·공무팀에 판넬 교체·라인 개조 견적부터 "
                   "부탁하는 게 빠릅니다."],
        ["관공서 판넬 시장", "관공서가 판넬을 물품으로 직접 산 계약입니다. 납품한 업체는 판넬을 "
                         "'파는' 쪽(경쟁사)이라 연락할 곳 목록에서 뺐습니다. 조달청 등록·"
                         "직접생산확인을 받으면 이 시장에 직접 팔 수 있습니다."],
        ["인쇄", "시트마다 A4 가로, 한 장 폭에 맞춰 두었습니다. 장마다 첫 줄이 다시 찍힙니다."],
    ], landscape=False)
    sheets = ([main, extra, detail] + ([factories] if factories.rows else [])
              + ([market] if market.rows else []) + [guide])
    return write_workbook(path, sheets)


def _steady_text(steady: float) -> str:
    """업종이 경기를 타는 정도 — 엑셀 칸에 짧게."""
    return "덜 탐" if steady >= 0.8 else ("많이 탐" if steady <= 0.4 else "보통")


def _axis(fit, key: str):
    return fit.axis(key) if fit else None


def write_makers_xlsx(makers, path: str | Path, within_km: float | None = None) -> Path:
    """기계·장비 제작사 목록을 인쇄용 엑셀로. 분야로 필터해 볼 수 있다."""
    from datetime import date
    from collections import Counter

    from prime_contractor.xlsx_writer import (
        CENTER, DECIMAL, TEXT, WRAP, Column, Sheet, write_workbook)

    main = Sheet("기계 제작사", [
        Column("순위", 5, CENTER), Column("회사 이름", 20, WRAP), Column("분야", 13, WRAP),
        Column("생산품", 30, WRAP), Column("지역", 7, CENTER), Column("거리(km)", 7, DECIMAL),
        Column("대표자", 8, CENTER), Column("전화", 13, CENTER), Column("주소", 30, WRAP),
    ], rows=[[i, c.name, c.sector, c.products, c.region, c.distance_km, c.ceo, c.phone,
              c.address] for i, c in enumerate(makers, 1)])
    fields = Counter(c.sector for c in makers).most_common()
    limit = f"{within_km:g}km 안" if within_km is not None else "거리 제한 없이"
    guide = Sheet("읽는 법", [Column("항목", 18, TEXT), Column("설명", 80, WRAP)], rows=[
        ["만든 날", date.today().isoformat()],
        ["담은 곳", f"안성에서 {limit}, 생산품이 기계·장비인 공장 {len(makers)}곳 (가까운 순)"],
        ["분야별", ", ".join(f"{f} {n}곳" for f, n in fields)],
        ["왜 이 회사들인가", "기계 하나마다 제어반이 하나씩 들어가고 같은 사양을 반복해서 밖에 "
                       "맡기는 곳이 많습니다. 작고 꾸준한 일이 나오는 고객입니다."],
        ["뺀 곳", "판넬을 만드는 곳(경쟁사), 부품·소재만 만드는 곳, 주소를 모르는 곳."],
        ["연락할 때", "'기계에 들어가는 제어반을 밖에 맡기시는지, 맡기신다면 견적 한번 내 보고 "
                    "싶다'로 시작하세요. 설계팀·생산팀·대표가 정하는 경우가 많습니다."],
        ["출처", "한국산업단지공단 전국 등록공장 현황(공공데이터포털·팩토리온). 생산품은 공장 "
               "등록 때 적은 것이라 지금과 다를 수 있습니다."],
    ], landscape=False)
    return write_workbook(path, [main, guide])

