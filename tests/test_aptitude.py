import pytest

from aptitude import MAJORS, QUESTIONS, TRAIT_ORDER, score_answers
from aptitude.scorer import _validate_answers


def _answers(default=3, **overrides):
    answers = {q.id: default for q in QUESTIONS}
    answers.update(overrides)
    return answers


def test_question_bank_covers_every_trait_evenly():
    assert len(QUESTIONS) == 36
    counts = {code: 0 for code in TRAIT_ORDER}
    for q in QUESTIONS:
        counts[q.trait] += 1
    assert all(count == 6 for count in counts.values())
    assert len(QUESTIONS) == len({q.id for q in QUESTIONS})


def test_all_neutral_answers_give_50_percent_every_trait():
    result = score_answers(_answers(default=3))
    assert all(score == 50.0 for score in result.trait_scores.values())


def test_maxing_out_one_trait_gives_it_the_top_score():
    r_ids = [q.id for q in QUESTIONS if q.trait == "R"]
    answers = _answers(default=1, **{qid: 5 for qid in r_ids})
    result = score_answers(answers)
    assert result.trait_scores["R"] == 100.0
    assert all(score == 0.0 for code, score in result.trait_scores.items() if code != "R")
    assert result.holland_code[0] == "R"


def test_top_recommendations_are_sorted_descending_by_match_score():
    result = score_answers(_answers(default=3))
    scores = [rec.match_score for rec in result.top_recommendations]
    assert scores == sorted(scores, reverse=True)


def test_top_n_is_respected():
    result = score_answers(_answers(default=3), top_n=3)
    assert len(result.top_recommendations) == 3


def test_missing_answer_raises_value_error():
    answers = _answers(default=3)
    del answers[QUESTIONS[0].id]
    with pytest.raises(ValueError, match="누락"):
        score_answers(answers)


def test_out_of_range_answer_raises_value_error():
    answers = _answers(default=3, **{QUESTIONS[0].id: 6})
    with pytest.raises(ValueError):
        score_answers(answers)


def test_non_integer_answer_raises_value_error():
    answers = _answers(default=3)
    answers[QUESTIONS[0].id] = "5"
    with pytest.raises(ValueError):
        _validate_answers(answers)


def test_every_major_trait_reference_is_valid():
    for major in MAJORS:
        assert major.primary in TRAIT_ORDER
        assert major.secondary is None or major.secondary in TRAIT_ORDER
