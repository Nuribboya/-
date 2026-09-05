import math

from stockscreener.analysis.financial_trend import analyze_financial_trend
from stockscreener.models import YearlyFinancials


def _make_year(fy, revenue=None, net_income=None, eps=None, free_cash_flow=None):
    return YearlyFinancials(
        fiscal_year=fy,
        revenue=revenue,
        net_income=net_income,
        eps=eps,
        free_cash_flow=free_cash_flow,
    )


def test_no_years_is_insufficient():
    trend = analyze_financial_trend([])
    assert trend.years_available == 0
    assert trend.insufficient_data is True
    assert trend.revenue_cagr is None


def test_below_minimum_years_is_insufficient_but_reports_loss_years():
    years = [
        _make_year(2020, revenue=100, net_income=-10, eps=-0.1, free_cash_flow=-5.0),
        _make_year(2021, revenue=110, net_income=5, eps=0.05, free_cash_flow=3.0),
    ]
    trend = analyze_financial_trend(years)
    assert trend.insufficient_data is True
    assert trend.loss_years == 1
    assert trend.eps_growth_pct is None
    # 추세 계산엔 데이터가 부족해도, 최근 연도 FCF 값 자체는 보여줄 수 있다
    assert trend.latest_free_cash_flow == 3.0
    assert trend.fcf_cagr is None


def test_cagr_and_eps_growth_computed_for_enough_years():
    years = [
        _make_year(2016, revenue=100, net_income=10, eps=1.0, free_cash_flow=8.0),
        _make_year(2017, revenue=120, net_income=12, eps=1.1, free_cash_flow=9.0),
        _make_year(2018, revenue=140, net_income=14, eps=1.2, free_cash_flow=10.0),
        _make_year(2019, revenue=160, net_income=16, eps=1.4, free_cash_flow=12.0),
        _make_year(2020, revenue=200, net_income=20, eps=2.0, free_cash_flow=16.0),
    ]
    trend = analyze_financial_trend(years)
    assert trend.insufficient_data is False
    assert trend.fiscal_year_range == (2016, 2020)
    assert trend.loss_years == 0

    expected_revenue_cagr = (200 / 100) ** (1 / 4) - 1
    expected_net_income_cagr = (20 / 10) ** (1 / 4) - 1
    expected_fcf_cagr = (16 / 8) ** (1 / 4) - 1
    assert math.isclose(trend.revenue_cagr, expected_revenue_cagr, rel_tol=1e-9)
    assert math.isclose(trend.net_income_cagr, expected_net_income_cagr, rel_tol=1e-9)
    assert math.isclose(trend.fcf_cagr, expected_fcf_cagr, rel_tol=1e-9)
    assert trend.latest_free_cash_flow == 16.0

    start_eps_avg = (1.0 + 1.1 + 1.2) / 3
    end_eps_avg = (1.2 + 1.4 + 2.0) / 3
    expected_growth = (end_eps_avg - start_eps_avg) / abs(start_eps_avg) * 100
    assert math.isclose(trend.eps_growth_pct, expected_growth, rel_tol=1e-9)
    assert trend.is_stable is True


def test_cagr_is_none_when_sign_changes():
    years = [
        _make_year(2016, revenue=100, net_income=-10, eps=-1.0),
        _make_year(2017, revenue=110, net_income=5, eps=0.5),
        _make_year(2018, revenue=120, net_income=8, eps=0.8),
        _make_year(2019, revenue=130, net_income=9, eps=0.9),
        _make_year(2020, revenue=140, net_income=12, eps=1.2),
    ]
    trend = analyze_financial_trend(years)
    # 시작 연도 순이익이 음수이므로 CAGR은 계산하지 않는다 (부호 반전은 의미 없음)
    assert trend.net_income_cagr is None
    assert trend.loss_years == 1


def test_is_stable_false_when_eps_growth_negative():
    years = [
        _make_year(2016, revenue=100, net_income=20, eps=2.0),
        _make_year(2017, revenue=100, net_income=18, eps=1.8),
        _make_year(2018, revenue=100, net_income=16, eps=1.6),
        _make_year(2019, revenue=100, net_income=14, eps=1.4),
        _make_year(2020, revenue=100, net_income=12, eps=1.2),
    ]
    trend = analyze_financial_trend(years)
    assert trend.eps_growth_pct < 0
    assert trend.is_stable is False
