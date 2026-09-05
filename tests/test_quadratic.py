from mathgrader.classify import ProblemType, classify
from mathgrader.solvers.quadratic import quadratic_steps


def test_quadratic_two_real_roots():
    ptype, data = classify("x^2 - 5x + 6 = 0")
    assert ptype is ProblemType.QUADRATIC_EQUATION
    steps, values = quadratic_steps(data["lhs_str"], data["rhs_str"], data["expr"], data["var"])
    assert set(values) == {2, 3}


def test_quadratic_double_root():
    ptype, data = classify("x^2 - 4x + 4 = 0")
    _, values = quadratic_steps(data["lhs_str"], data["rhs_str"], data["expr"], data["var"])
    assert values == [2]


def test_quadratic_no_real_roots_returns_complex():
    ptype, data = classify("x^2 + x + 1 = 0")
    _, values = quadratic_steps(data["lhs_str"], data["rhs_str"], data["expr"], data["var"])
    assert len(values) == 2
