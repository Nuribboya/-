import sympy as sp

from mathgrader.classify import ProblemType, classify
from mathgrader.solvers.simplify import simplify_steps


def test_simplify_like_terms():
    ptype, data = classify("3x + 5 - x + 2")
    assert ptype is ProblemType.SIMPLIFY
    _, value = simplify_steps(data["text"], data["expr"])
    x = sp.symbols("x")
    assert sp.simplify(value - (2 * x + 7)) == 0


def test_simplify_expand():
    ptype, data = classify("2*(x + 3)")
    _, value = simplify_steps(data["text"], data["expr"])
    x = sp.symbols("x")
    assert sp.simplify(value - (2 * x + 6)) == 0
