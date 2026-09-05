"""변수를 포함한 식을 전개/동류항 정리하는 단계별 풀이."""
from sympy import expand, simplify as sp_simplify


def simplify_steps(text: str, expr):
    """(steps, 정리된 식) 을 반환한다."""
    steps = [f"원래 식: {text.strip()}"]

    expanded = expand(expr)
    if expanded != expr:
        steps.append(f"전개하면: {expanded}")

    simplified = sp_simplify(expanded)
    steps.append(f"동류항끼리 정리하면: {simplified}")
    return steps, simplified
