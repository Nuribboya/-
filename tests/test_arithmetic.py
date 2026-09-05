import pytest
import sympy as sp

from mathgrader.solvers.arithmetic import arithmetic_steps


def test_order_of_operations():
    steps, value = arithmetic_steps("3 + 4 * 2 - 1")
    assert value == 10
    assert len(steps) == 3


def test_parentheses():
    steps, value = arithmetic_steps("(3 + 4) * 2")
    assert value == 14


def test_division_fraction():
    steps, value = arithmetic_steps("7 / 2")
    assert value == sp.Rational(7, 2)


def test_power():
    steps, value = arithmetic_steps("2 ^ 3 + 1")
    assert value == 9


def test_division_by_zero():
    with pytest.raises(ValueError):
        arithmetic_steps("1 / 0")


def test_rejects_non_numeric_input():
    with pytest.raises(ValueError):
        arithmetic_steps("__import__('os')")
