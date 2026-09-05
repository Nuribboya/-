"""이차방정식(ax^2 + bx + c = 0 형태) 단계별 풀이 (근의 공식)."""
from sympy import Poly, simplify, sqrt


def _format_term(coef, power, var) -> str:
    if power == 2:
        base = f"{var}^2" if coef == 1 else (f"-{var}^2" if coef == -1 else f"{coef}*{var}^2")
    elif power == 1:
        base = f"{var}" if coef == 1 else (f"-{var}" if coef == -1 else f"{coef}*{var}")
    else:
        base = f"{coef}"
    return base


def _format_quadratic(a, b, c, var) -> str:
    parts = [_format_term(a, 2, var)]
    parts.append(f"+ {_format_term(b, 1, var)}" if b >= 0 else f"- {_format_term(-b, 1, var)}")
    parts.append(f"+ {c}" if c >= 0 else f"- {-c}")
    return " ".join(parts)


def quadratic_steps(lhs_str: str, rhs_str: str, expr, var):
    """(steps, 해 목록) 을 반환한다. 실근이 없으면 복소수 해를 반환한다."""
    poly = Poly(expr, var)
    a, b, c = poly.all_coeffs()

    steps = [f"주어진 방정식: {lhs_str.strip()} = {rhs_str.strip()}"]
    steps.append(f"표준형으로 정리하면: {_format_quadratic(a, b, c, var)} = 0")

    discriminant = simplify(b**2 - 4 * a * c)
    steps.append(f"판별식 D = b^2 - 4ac = ({b})^2 - 4*({a})*({c}) = {discriminant}")
    steps.append(f"근의 공식: {var} = (-({b}) ± √D) / (2*{a})")

    sqrt_d = sqrt(discriminant)
    x1 = simplify((-b + sqrt_d) / (2 * a))
    x2 = simplify((-b - sqrt_d) / (2 * a))

    if discriminant > 0:
        steps.append("D > 0 이므로 서로 다른 두 실근을 가집니다.")
        steps.append(f"{var} = {x1} 또는 {var} = {x2}")
        return steps, [x1, x2]
    if discriminant == 0:
        steps.append("D = 0 이므로 중근을 가집니다.")
        steps.append(f"{var} = {x1}")
        return steps, [x1]

    steps.append("D < 0 이므로 실근은 없고, 복소수 해를 가집니다.")
    steps.append(f"{var} = {x1} 또는 {var} = {x2}")
    return steps, [x1, x2]
