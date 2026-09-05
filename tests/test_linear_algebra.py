import pytest
import sympy as sp

from mathgrader.solvers.linear_algebra import (
    determinant_steps,
    eigen_steps,
    inverse_steps,
    linear_system_steps,
    parse_matrix_literal,
)


def test_parse_matrix_literal():
    m = parse_matrix_literal("[[1,2],[3,4]]")
    assert m.tolist() == [[1, 2], [3, 4]]


def test_parse_matrix_literal_rejects_code():
    with pytest.raises(ValueError):
        parse_matrix_literal("__import__('os').system('echo hi')")


def test_determinant_2x2():
    m = parse_matrix_literal("[[1,2],[3,4]]")
    _, value = determinant_steps(m)
    assert value == -2


def test_inverse():
    m = parse_matrix_literal("[[1,2],[3,4]]")
    _, inv = inverse_steps(m)
    assert inv.tolist() == [[-2, 1], [sp.Rational(3, 2), sp.Rational(-1, 2)]]


def test_inverse_singular_matrix():
    m = parse_matrix_literal("[[1,2],[2,4]]")
    steps, inv = inverse_steps(m)
    assert inv is None


def test_eigenvalues_diagonal():
    m = parse_matrix_literal("[[2,0],[0,3]]")
    _, eigenvals = eigen_steps(m)
    assert set(eigenvals.keys()) == {2, 3}


def test_linear_system_unique_solution():
    _, result = linear_system_steps("x+y=3; 2x-y=0")
    variables = result["variables"]
    solution = result["solution"]
    x, y = sp.symbols("x y")
    assert solution[x] == 1
    assert solution[y] == 2
    assert variables == [x, y]
