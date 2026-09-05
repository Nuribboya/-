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
    DERIVATIVE = "derivative"
    INTEGRAL = "integral"
    LIMIT = "limit"
    TAYLOR_SERIES = "taylor_series"
    SERIES_SUM = "series_sum"
    MATRIX_DETERMINANT = "matrix_determinant"
    MATRIX_INVERSE = "matrix_inverse"
    MATRIX_EIGEN = "matrix_eigen"
    LINEAR_SYSTEM = "linear_system"
    ODE = "ode"
    PERMUTATION = "permutation"
    COMBINATION = "combination"


# "키워드: 인자, 인자" 형태로 입력된 고급 주제(대학 수학)를 유형별로 분기한다.
_KEYWORD_ALIASES = {
    "미분": ProblemType.DERIVATIVE,
    "편미분": ProblemType.DERIVATIVE,
    "derivative": ProblemType.DERIVATIVE,
    "diff": ProblemType.DERIVATIVE,
    "적분": ProblemType.INTEGRAL,
    "정적분": ProblemType.INTEGRAL,
    "부정적분": ProblemType.INTEGRAL,
    "integral": ProblemType.INTEGRAL,
    "integrate": ProblemType.INTEGRAL,
    "극한": ProblemType.LIMIT,
    "limit": ProblemType.LIMIT,
    "lim": ProblemType.LIMIT,
    "테일러": ProblemType.TAYLOR_SERIES,
    "매클로린": ProblemType.TAYLOR_SERIES,
    "taylor": ProblemType.TAYLOR_SERIES,
    "maclaurin": ProblemType.TAYLOR_SERIES,
    "급수합": ProblemType.SERIES_SUM,
    "급수": ProblemType.SERIES_SUM,
    "series": ProblemType.SERIES_SUM,
    "sum": ProblemType.SERIES_SUM,
    "행렬식": ProblemType.MATRIX_DETERMINANT,
    "det": ProblemType.MATRIX_DETERMINANT,
    "determinant": ProblemType.MATRIX_DETERMINANT,
    "역행렬": ProblemType.MATRIX_INVERSE,
    "inverse": ProblemType.MATRIX_INVERSE,
    "inv": ProblemType.MATRIX_INVERSE,
    "고유값": ProblemType.MATRIX_EIGEN,
    "고윳값": ProblemType.MATRIX_EIGEN,
    "eigen": ProblemType.MATRIX_EIGEN,
    "eigenvalue": ProblemType.MATRIX_EIGEN,
    "연립방정식": ProblemType.LINEAR_SYSTEM,
    "연립": ProblemType.LINEAR_SYSTEM,
    "linsolve": ProblemType.LINEAR_SYSTEM,
    "system": ProblemType.LINEAR_SYSTEM,
    "미분방정식": ProblemType.ODE,
    "dsolve": ProblemType.ODE,
    "ode": ProblemType.ODE,
    "순열": ProblemType.PERMUTATION,
    "npr": ProblemType.PERMUTATION,
    "permutation": ProblemType.PERMUTATION,
    "조합": ProblemType.COMBINATION,
    "ncr": ProblemType.COMBINATION,
    "combination": ProblemType.COMBINATION,
}

_KEYWORD_RE = re.compile(r"^\s*([^\s:]+)\s*:\s*(.+)$", re.DOTALL)

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
    raw = problem_text.strip()
    if not raw:
        raise ValueError("문제가 비어 있습니다.")

    keyword_match = _KEYWORD_RE.match(raw)
    if keyword_match:
        keyword = keyword_match.group(1).strip().lower()
        problem_type = _KEYWORD_ALIASES.get(keyword)
        if problem_type is not None:
            return problem_type, {"args": keyword_match.group(2).strip()}

    text = _strip_question_suffix(raw)
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
