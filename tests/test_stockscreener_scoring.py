from stockscreener.analysis.scoring import composite_score, score_category
from stockscreener.models import GrahamAnalysis, GrahamCriterion, ValuationResult


def _valuation(mos):
    return ValuationResult(
        ticker="T",
        price=10.0,
        eps_used=2.0,
        growth_rate_pct_used=5.0,
        intrinsic_value=20.0,
        graham_number=15.0,
        margin_of_safety=mos,
        price_below_graham_number=True,
        is_undervalued=mos is not None and mos >= 0.25,
    )


def _graham(passed, evaluable):
    criteria = [
        GrahamCriterion(f"c{i}", f"기준{i}", True, "ok") for i in range(passed)
    ] + [
        GrahamCriterion(f"c{i}", f"기준{i}", False, "no")
        for i in range(evaluable - passed)
    ]
    return GrahamAnalysis(ticker="T", graham_number=15.0, criteria=tuple(criteria))


def test_neutral_when_no_data():
    score = composite_score(None, None)
    assert score == 50.0
    assert score_category(score) == "적정~관망"


def test_high_margin_and_all_criteria_pass_scores_high():
    score = composite_score(_valuation(0.5), _graham(7, 7))
    assert score > 65
    assert score_category(score) == "저평가 후보"


def test_negative_margin_and_no_criteria_pass_scores_low():
    score = composite_score(_valuation(-0.5), _graham(0, 7))
    assert score < 40
    assert score_category(score) == "고평가/주의"


def test_score_is_clamped_between_0_and_100():
    assert 0.0 <= composite_score(_valuation(-5.0), _graham(0, 7)) <= 100.0
    assert 0.0 <= composite_score(_valuation(5.0), _graham(7, 7)) <= 100.0


def test_score_ignores_criteria_with_no_evaluable_data():
    graham = GrahamAnalysis(ticker="T", graham_number=None, criteria=())
    score = composite_score(_valuation(None), graham)
    assert score == 50.0
