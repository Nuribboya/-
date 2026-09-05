import math

from stockscreener.analysis.graham import evaluate_graham_criteria, graham_number
from stockscreener.models import YearlyFinancials


def test_graham_number_normal_case():
    assert math.isclose(graham_number(2.0, 10.0), math.sqrt(22.5 * 2.0 * 10.0))


def test_graham_number_none_when_missing_or_nonpositive():
    assert graham_number(None, 10.0) is None
    assert graham_number(2.0, None) is None
    assert graham_number(0.0, 10.0) is None
    assert graham_number(2.0, -1.0) is None


def _healthy_years(n=6):
    years = []
    for i in range(n):
        fy = 2015 + i
        eps = 1.0 + 0.2 * i
        years.append(
            YearlyFinancials(
                fiscal_year=fy,
                revenue=1000.0 + 100 * i,
                net_income=100.0 + 20 * i,
                eps=eps,
                book_value_per_share=10.0 + i,
                total_current_assets=500.0,
                total_current_liabilities=200.0,
                total_liabilities=600.0,
                long_term_debt=100.0,
                dividend_per_share=0.5,
            )
        )
    return years


def test_evaluate_graham_criteria_all_pass_when_healthy_and_cheap():
    years = _healthy_years(6)
    # 낮은 주가로 PER/PBR 관련 기준까지 통과시키기 위한 값
    analysis = evaluate_graham_criteria("TEST", years, price=10.0)

    by_key = {c.key: c for c in analysis.criteria}
    assert by_key["earnings_stability"].passed is True
    assert by_key["dividend_record"].passed is True
    assert by_key["current_ratio"].passed is True
    assert by_key["debt_vs_working_capital"].passed is True
    assert analysis.graham_number is not None
    assert analysis.evaluable_count == len(analysis.criteria)


def test_evaluate_graham_criteria_marks_insufficient_data_as_none_not_fail():
    years = _healthy_years(3)  # MIN_YEARS_FOR_TREND(5) 미만
    analysis = evaluate_graham_criteria("TEST", years, price=10.0)

    by_key = {c.key: c for c in analysis.criteria}
    assert by_key["earnings_stability"].passed is None
    assert by_key["dividend_record"].passed is None
    assert by_key["eps_growth"].passed is None
    # 데이터 부족은 "실패"가 아니라 "판단 불가"로 표기되어야 한다
    assert analysis.passed_count <= analysis.evaluable_count
    assert analysis.evaluable_count < len(analysis.criteria)


def test_evaluate_graham_criteria_handles_loss_year_and_no_dividend():
    years = _healthy_years(6)
    years[-1] = YearlyFinancials(
        fiscal_year=years[-1].fiscal_year,
        revenue=1000.0,
        net_income=-50.0,
        eps=-0.5,
        book_value_per_share=5.0,
        total_current_assets=500.0,
        total_current_liabilities=200.0,
        total_liabilities=600.0,
        long_term_debt=100.0,
        dividend_per_share=0.0,
    )
    analysis = evaluate_graham_criteria("TEST", years, price=10.0)
    by_key = {c.key: c for c in analysis.criteria}
    assert by_key["earnings_stability"].passed is False
    assert by_key["dividend_record"].passed is False
    # 음수 EPS/BVPS로는 그레이엄 넘버를 계산할 수 없어야 한다
    assert analysis.graham_number is None


def test_evaluate_graham_criteria_no_years_does_not_crash():
    analysis = evaluate_graham_criteria("TEST", [], price=10.0)
    assert analysis.graham_number is None
    assert all(c.passed is None for c in analysis.criteria)
