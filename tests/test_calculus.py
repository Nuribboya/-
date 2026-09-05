import sympy as sp

from mathgrader.solvers.calculus import (
    derivative_steps,
    integral_steps,
    limit_steps,
    series_sum_steps,
    taylor_steps,
)

x, y = sp.symbols("x y")


def test_derivative_single_variable():
    steps, value = derivative_steps("x^3 + 2*x, x")
    assert sp.simplify(value - (3 * x**2 + 2)) == 0
    assert len(steps) == 2


def test_derivative_second_order():
    _, value = derivative_steps("x^4, x, 2")
    assert sp.simplify(value - 12 * x**2) == 0


def test_partial_derivative():
    steps, value = derivative_steps("x^2*y + y^3, x")
    assert sp.simplify(value - 2 * x * y) == 0
    assert "편미분" in steps[0]


def test_indefinite_integral():
    _, value = integral_steps("x^2, x")
    assert sp.simplify(value - x**3 / 3) == 0


def test_definite_integral():
    _, value = integral_steps("x^2, x, 0, 1")
    assert value == sp.Rational(1, 3)


def test_limit_basic():
    _, value = limit_steps("sin(x)/x, x, 0")
    assert value == 1


def test_limit_infinity():
    _, value = limit_steps("1/x, x, oo")
    assert value == 0


def test_taylor_series():
    _, value = taylor_steps("sin(x), x, 0, 5")
    expected = x - x**3 / 6 + x**5 / 120
    assert sp.simplify(value - expected) == 0


def test_series_sum_convergent():
    _, value = series_sum_steps("1/n^2, n, 1, oo")
    n = sp.symbols("n")
    assert sp.simplify(value - sp.pi**2 / 6) == 0
