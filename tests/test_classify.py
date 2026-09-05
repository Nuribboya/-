from mathgrader.classify import ProblemType, classify


def test_strip_question_suffix_for_arithmetic():
    ptype, data = classify("3 + 4 * 2 = ?")
    assert ptype is ProblemType.ARITHMETIC
    assert data["text"] == "3 + 4 * 2"


def test_plain_arithmetic():
    ptype, _ = classify("3 + 4 * 2")
    assert ptype is ProblemType.ARITHMETIC


def test_simplify_classification():
    ptype, _ = classify("3x + 5 - x")
    assert ptype is ProblemType.SIMPLIFY


def test_generic_equation_no_variable():
    ptype, data = classify("3 + 2 = 5")
    assert ptype is ProblemType.GENERIC_EQUATION
    assert data["var"] is None
