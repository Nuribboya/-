"""미분방정식: 분류 + sympy dsolve, 학생 답안은 대입 검증(checkodesol) 방식으로 채점."""
import re

import sympy as sp

from ..parsing import parse_expression

_x = sp.Symbol("x")
_y = sp.Function("y")


def _preprocess(text: str) -> str:
    """y'', y', y 를 각각 Derivative(y(x),x,2), Derivative(y(x),x), y(x) 로 치환한다."""
    t = text.strip()
    t = re.sub(r"y\s*''", "Derivative(y(x), x, 2)", t)
    t = re.sub(r"y\s*'", "Derivative(y(x), x)", t)
    t = re.sub(r"(?<![\w)])y(?!\()", "y(x)", t)
    return t


def ode_steps(args_text: str):
    """(steps, 채점용 데이터) 를 반환한다."""
    processed = _preprocess(args_text)
    if "=" not in processed:
        raise ValueError("형식: y' + y = 0 처럼 등호가 포함된 미분방정식을 입력하세요.")

    lhs_str, rhs_str = processed.split("=", 1)
    local_dict = {"y": _y, "x": _x, "Derivative": sp.Derivative}
    lhs = parse_expression(lhs_str, local_dict=local_dict)
    rhs = parse_expression(rhs_str, local_dict=local_dict)
    equation = sp.Eq(lhs, rhs)

    steps = [f"주어진 미분방정식: {args_text.strip()}"]

    try:
        hints = sp.classify_ode(equation, _y(_x))
    except Exception:
        hints = ()
    method = hints[0] if hints else "일반적인 방법"
    steps.append(f"분류된 풀이법: {method}")

    try:
        solution = sp.dsolve(equation, _y(_x))
    except NotImplementedError as exc:
        raise ValueError("이 미분방정식은 자동으로 풀 수 없습니다.") from exc
    steps.append(f"일반해: {solution}")

    return steps, {"equation": equation, "y": _y(_x)}


def verify_ode_answer(verification_data: dict, student_answer: str):
    """학생이 적은 y = f(x, C1, ...) 를 원래 미분방정식에 대입해 만족하는지 검증한다."""
    text = student_answer.strip()
    if "=" in text:
        text = text.split("=", 1)[1]
    candidate = parse_expression(text, local_dict={"x": _x})

    equation = verification_data["equation"]
    y_func = verification_data["y"]
    candidate_eq = sp.Eq(y_func, candidate)

    result = sp.checkodesol(equation, candidate_eq)
    if isinstance(result, list):
        result = result[0] if result else (False, None)
    is_ok, _residual = result
    return bool(is_ok)
