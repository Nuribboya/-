"""일차/이차식으로 분류되지 않은 방정식에 대한 기본 처리."""
from sympy import simplify, solve


def generic_steps(lhs_str: str, rhs_str: str, expr, var):
    """(steps, 해 목록 또는 None) 을 반환한다.

    - 변수가 하나지만 3차 이상인 경우: sympy.solve 로 해를 구해 보여준다.
    - 변수가 없는 경우: 좌변/우변이 같은 값인지(참/거짓)만 알려준다.
    - 변수가 여러 개인 경우: 자동 채점을 지원하지 않는다고 안내한다.
    """
    steps = [f"주어진 방정식: {lhs_str.strip()} = {rhs_str.strip()}"]

    if var is not None:
        solutions = solve(expr, var)
        if not solutions:
            steps.append("이 방정식은 실수 범위에서 해가 없습니다.")
            return steps, []
        steps.append(
            "차수가 높은 방정식이라 단계별 풀이 대신 대수적으로 해를 구하면: "
            f"{var} = {', '.join(str(s) for s in solutions)}"
        )
        return steps, solutions

    if expr is not None and expr.free_symbols:
        steps.append("변수가 여러 개 포함되어 있어 자동으로 하나의 해를 구할 수 없습니다.")
        return steps, None

    value = simplify(expr) if expr is not None else None
    if value == 0:
        steps.append("좌변과 우변의 값이 같습니다. (참)")
    else:
        steps.append(f"좌변과 우변의 값이 다릅니다. (거짓, 차이: {value})")
    return steps, None
