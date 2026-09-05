import json

from stockscreener import report as report_fmt
from stockscreener.models import (
    FinancialTrend,
    GrahamAnalysis,
    GrahamCriterion,
    NewsItem,
    PriceSnapshot,
    StockReport,
    ValuationResult,
)


def _sample_report():
    trend = FinancialTrend(
        years_available=6,
        fiscal_year_range=(2015, 2020),
        revenue_cagr=0.1,
        net_income_cagr=0.12,
        eps_growth_pct=40.0,
        loss_years=0,
        is_stable=True,
        insufficient_data=False,
    )
    graham = GrahamAnalysis(
        ticker="TEST",
        graham_number=21.2,
        criteria=(
            GrahamCriterion("current_ratio", "유동비율 2.0 이상", True, "유동비율 2.50"),
            GrahamCriterion("dividend_record", "연속 배당 기록", None, "데이터 부족"),
        ),
    )
    valuation = ValuationResult(
        ticker="TEST",
        price=5.0,
        eps_used=2.0,
        growth_rate_pct_used=5.0,
        intrinsic_value=37.0,
        graham_number=21.2,
        margin_of_safety=0.865,
        price_below_graham_number=True,
        is_undervalued=True,
    )
    return StockReport(
        ticker="TEST",
        price=PriceSnapshot(ticker="TEST", price=5.0, currency="USD"),
        trend=trend,
        graham=graham,
        valuation=valuation,
        news=(NewsItem(title="테스트 뉴스", link="https://example.com", source="news:test"),),
    )


def test_format_stock_report_contains_key_figures():
    text = report_fmt.format_stock_report(_sample_report())
    assert "TEST" in text
    assert "저평가 여부: 예" in text
    assert "PASS" in text
    assert "N/A" in text  # 판단 불가 항목 표시


def test_format_stock_report_error_case_short_circuits():
    err_report = StockReport(
        ticker="BAD",
        price=PriceSnapshot(ticker="BAD", price=None),
        trend=None,
        graham=None,
        valuation=None,
        error="시세 조회 실패",
    )
    text = report_fmt.format_stock_report(err_report)
    assert "오류" in text
    assert "시세 조회 실패" in text


def test_format_market_news_handles_empty():
    assert "가져오지 못했습니다" in report_fmt.format_market_news([])


def test_format_undervalued_list_empty_and_nonempty():
    empty_text = report_fmt.format_undervalued_list([])
    assert "없습니다" in empty_text

    text = report_fmt.format_undervalued_list([_sample_report()])
    assert "TEST" in text


def test_to_json_round_trips():
    payload = report_fmt.to_json([_sample_report()], [])
    data = json.loads(payload)
    assert data["reports"][0]["ticker"] == "TEST"
    assert data["reports"][0]["valuation"]["is_undervalued"] is True
