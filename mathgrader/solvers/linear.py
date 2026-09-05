"""일차방정식(ax + b = c 형태) 단계별 풀이."""
from sympy import Poly, simplify


def _format_ax_plus_b(a, b, var) -> str:
    if a == 1:
        term = f"{var}"
    elif a == -1:
        term = f"-{var}"
    else:
        term = f"{a}*{var}"
    if b > 0:
        return f"{term} + {b}"
    if b < 0:
        return f"{term} - {-b}"
    return term


def _format_ax(a, var) -> str:
    if a == 1:
        return f"{var}"
    if a == -1:
        return f"-{var}"
    return f"{a}*{var}"


def linear_steps(lhs_str: str, rhs_str: str, expr, var):
    """(steps, 해) 를 반환한다. expr 은 이항 정리된 lhs-rhs (== a*var + b)."""
    poly = Poly(expr, var)
    a, b = poly.all_coeffs()

    steps = [f"주어진 방정식: {lhs_str.strip()} = {rhs_str.strip()}"]
    steps.append(f"모든 항을 좌변으로 이항하여 정리하면: {_format_ax_plus_b(a, b, var)} = 0")
    if b != 0:
        steps.append(f"상수항을 우변으로 이항하면: {_format_ax(a, var)} = {-b}")

    solution = simplify(-b / a)
    steps.append(f"양변을 {a}로 나누면: {var} = {solution}")
    return steps, solution
