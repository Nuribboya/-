from mathgrader.classify import ProblemType, classify
from mathgrader.solvers.linear import linear_steps


def test_linear_solve():
    ptype, data = classify("2x + 3 = 11")
    assert ptype is ProblemType.LINEAR_EQUATION
    steps, value = linear_steps(data["lhs_str"], data["rhs_str"], data["expr"], data["var"])
    assert value == 4
    assert len(steps) >= 2


def test_linear_variables_both_sides():
    ptype, data = classify("3x + 2 = x + 10")
    assert ptype is ProblemType.LINEAR_EQUATION
    _, value = linear_steps(data["lhs_str"], data["rhs_str"], data["expr"], data["var"])
    assert value == 4


def test_linear_negative_solution():
    ptype, data = classify("5x + 10 = 0")
    _, value = linear_steps(data["lhs_str"], data["rhs_str"], data["expr"], data["var"])
    assert value == -2
