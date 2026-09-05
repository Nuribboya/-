"""분석 결과를 사람이 읽기 좋은 텍스트/JSON으로 변환한다."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional, Sequence

from stockscreener.analysis.scoring import composite_score, score_category
from stockscreener.models import NewsItem, StockReport


def _fmt(value: Optional[float], digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{digits}f}{suffix}"


def format_stock_report(report: StockReport) -> str:
    lines = [f"## {report.ticker}"]
    if not report.ok:
        lines.append(f"  오류: {report.error}")
        return "\n".join(lines)

    lines.append(f"  현재가: {_fmt(report.price.price)} {report.price.currency}")

    if report.trend:
        t = report.trend
        if t.insufficient_data:
            lines.append(f"  재무 추세: 데이터 부족 (확보 {t.years_available}개년)")
        else:
            lines.append(
                "  재무 추세 ({}~{}, {}개년): 매출 CAGR {}, 순이익 CAGR {}, EPS 성장률 {}, 적자연도 {}개".format(
                    t.fiscal_year_range[0],
                    t.fiscal_year_range[1],
                    t.years_available,
                    _fmt(t.revenue_cagr * 100 if t.revenue_cagr is not None else None, 1, "%"),
                    _fmt(t.net_income_cagr * 100 if t.net_income_cagr is not None else None, 1, "%"),
                    _fmt(t.eps_growth_pct, 1, "%"),
                    t.loss_years,
                )
            )

    if report.graham:
        g = report.graham
        lines.append(
            f"  그레이엄 기준: {g.passed_count}/{g.evaluable_count} 충족 "
            f"(그레이엄 넘버: {_fmt(g.graham_number)})"
        )
        for c in g.criteria:
            mark = "PASS" if c.passed is True else ("FAIL" if c.passed is False else "N/A")
            lines.append(f"    [{mark}] {c.label} - {c.detail}")

    if report.valuation:
        v = report.valuation
        lines.append(
            "  내재가치: {} (추정 성장률 {}), 안전마진: {}, 저평가 여부: {}".format(
                _fmt(v.intrinsic_value),
                _fmt(v.growth_rate_pct_used, 1, "%"),
                _fmt(
                    v.margin_of_safety * 100 if v.margin_of_safety is not None else None, 1, "%"
                ),
                "예" if v.is_undervalued else "아니오",
            )
        )

    if report.news:
        lines.append("  관련 뉴스:")
        for item in report.news:
            lines.append(f"    - {item.title} ({item.source})")

    return "\n".join(lines)


def format_market_news(news: Sequence[NewsItem]) -> str:
    if not news:
        return "## 증시 영향 뉴스\n  가져오지 못했습니다 (네트워크 또는 피드 문제)."
    lines = ["## 증시 영향 뉴스 (실시간 갱신)"]
    for item in news:
        published = (
            item.published.strftime("%Y-%m-%d %H:%M UTC") if item.published else "시간 미상"
        )
        lines.append(f"  - [{published}] {item.title} ({item.source})")
    return "\n".join(lines)


def format_undervalued_list(reports: Sequence[StockReport]) -> str:
    if not reports:
        return "## 저평가 종목 목록\n  조건을 충족하는 종목이 없습니다."
    lines = ["## 저평가 종목 목록 (안전마진 높은 순)"]
    for r in reports:
        v = r.valuation
        lines.append(
            "  - {}: 주가 {}, 내재가치 {}, 안전마진 {}, 그레이엄 넘버 {}".format(
                r.ticker,
                _fmt(v.price),
                _fmt(v.intrinsic_value),
                _fmt(
                    v.margin_of_safety * 100 if v.margin_of_safety is not None else None, 1, "%"
                ),
                _fmt(v.graham_number),
            )
        )
    return "\n".join(lines)


def _report_to_dict(report: StockReport) -> dict:
    data = asdict(report)
    if report.ok:
        score = composite_score(report.valuation, report.graham)
        data["composite_score"] = score
        data["score_category"] = score_category(score)
    else:
        data["composite_score"] = None
        data["score_category"] = None
    return data


def to_json(reports: Sequence[StockReport], market_news: Sequence[NewsItem]) -> str:
    def default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports": [_report_to_dict(r) for r in reports],
        "market_news": [asdict(n) for n in market_news],
    }
    return json.dumps(payload, default=default, ensure_ascii=False, indent=2)
