"""입력된 문제 텍스트가 어떤 유형인지 분류한다."""
import re
from enum import Enum

from sympy import Poly, simplify

from .parsing import parse_expression


class ProblemType(str, Enum):
    ARITHMETIC = "arithmetic"
    LINEAR_EQUATION = "linear_equation"
    QUADRATIC_EQUATION = "quadratic_equation"
    SIMPLIFY = "simplify"
    GENERIC_EQUATION = "generic_equation"


# "3 + 4 * 2 = ?" 처럼 답을 구하라는 의미로 붙는 꼬리표는 방정식의 '=' 로 오해하지 않도록 제거
_TRAILING_Q_RE = re.compile(r"=\s*(\?|얼마|얼마인가|얼마입니까)?\s*[.?]*\s*$")


def _strip_question_suffix(text: str) -> str:
    text = text.strip()
    match = _TRAILING_Q_RE.search(text)
    if match and match.group(0).strip().startswith("="):
        return text[: match.start()].strip()
    return text


def classify(problem_text: str):
    """문제를 분류하고, 각 풀이 함수에 필요한 데이터를 함께 반환한다."""
    text = _strip_question_suffix(problem_text)
    if not text:
        raise ValueError("문제가 비어 있습니다.")

    if "=" in text:
        lhs_str, rhs_str = text.split("=", 1)
        lhs = parse_expression(lhs_str)
        rhs = parse_expression(rhs_str)
        expr = simplify(lhs - rhs)
        free = expr.free_symbols

        if len(free) == 1:
            var = next(iter(free))
            poly = Poly(expr, var)
            degree = poly.degree()
            data = {"lhs_str": lhs_str, "rhs_str": rhs_str, "expr": expr, "var": var}
            if degree == 1:
                return ProblemType.LINEAR_EQUATION, data
            if degree == 2:
                return ProblemType.QUADRATIC_EQUATION, data
            return ProblemType.GENERIC_EQUATION, data

        # 변수가 없거나(단순 참/거짓 판정) 여러 개인 경우는 범용 처리로 보낸다
        return ProblemType.GENERIC_EQUATION, {
            "lhs_str": lhs_str,
            "rhs_str": rhs_str,
            "expr": expr,
            "var": None,
        }

    expr = parse_expression(text)
    if expr.free_symbols:
        return ProblemType.SIMPLIFY, {"text": text, "expr": expr}
    return ProblemType.ARITHMETIC, {"text": text}
