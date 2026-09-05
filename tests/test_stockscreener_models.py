from stockscreener.models import YearlyFinancials


def test_debt_to_equity_basic():
    y = YearlyFinancials(fiscal_year=2020, total_liabilities=600.0, total_equity=800.0)
    assert y.debt_to_equity == 0.75


def test_debt_to_equity_none_when_missing_or_zero_equity():
    assert YearlyFinancials(fiscal_year=2020, total_liabilities=600.0).debt_to_equity is None
    assert (
        YearlyFinancials(fiscal_year=2020, total_liabilities=600.0, total_equity=0.0).debt_to_equity
        is None
    )
    assert (
        YearlyFinancials(fiscal_year=2020, total_equity=800.0).debt_to_equity is None
    )
