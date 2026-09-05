"""괄호/연산자 우선순위를 지키는 산술식 단계별 풀이.

Python의 ``ast`` 모듈로 수식을 파싱한 뒤, 상수/이항연산/단항연산 노드만
허용하는 화이트리스트 방식으로 직접 순회한다 (``eval``/``exec``는 사용하지
않는다). 자식 노드부터 재귀적으로 계산하는 후위 순회이므로, 괄호와 연산자
우선순위가 이미 트리 구조에 반영되어 있어 곧 표준적인 "연산 순서"가 된다.
"""
import ast

import sympy as sp

_OPS = {
    ast.Add: ("+", lambda a, b: a + b),
    ast.Sub: ("-", lambda a, b: a - b),
    ast.Mult: ("×", lambda a, b: a * b),
    ast.Div: ("÷", lambda a, b: a / b),
    ast.Pow: ("^", lambda a, b: a**b),
}


def _fmt(value) -> str:
    return str(sp.nsimplify(value))


def _to_rational(node: ast.Constant) -> sp.Rational:
    value = node.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("숫자가 아닌 값이 포함되어 있습니다.")
    if isinstance(value, int):
        return sp.Integer(value)
    return sp.Rational(str(value))


def _eval(node, steps: list):
    if isinstance(node, ast.Expression):
        return _eval(node.body, steps)

    if isinstance(node, ast.Constant):
        value = _to_rational(node)
        return value, _fmt(value)

    if isinstance(node, ast.UnaryOp):
        value, text = _eval(node.operand, steps)
        if isinstance(node.op, ast.USub):
            result = -value
            steps.append(f"-{text} = {_fmt(result)}")
            return result, _fmt(result)
        if isinstance(node.op, ast.UAdd):
            return value, text
        raise ValueError("지원하지 않는 연산입니다.")

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _OPS:
            raise ValueError("지원하지 않는 연산입니다.")
        symbol, func = _OPS[op_type]
        left_val, left_text = _eval(node.left, steps)
        right_val, right_text = _eval(node.right, steps)
        if op_type is ast.Div and right_val == 0:
            raise ValueError("0으로 나눌 수 없습니다.")
        result = func(left_val, right_val)
        steps.append(f"{left_text} {symbol} {right_text} = {_fmt(result)}")
        return result, _fmt(result)

    raise ValueError("지원하지 않는 수식입니다. 숫자와 + - * / ^ () 만 사용할 수 있습니다.")


def arithmetic_steps(text: str):
    """산술식을 단계별로 계산한다. (steps, 최종값) 을 반환한다."""
    cleaned = text.replace("×", "*").replace("÷", "/").replace("^", "**").strip()
    if not cleaned:
        raise ValueError("빈 입력입니다.")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"'{text}'을(를) 수식으로 해석할 수 없습니다.") from exc

    steps: list = []
    value, _ = _eval(tree, steps)
    if not steps:
        steps.append(f"{_fmt(value)} (계산할 연산이 없습니다)")
    return steps, sp.nsimplify(value)
