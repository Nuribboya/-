"""숫자 상수 + 사칙연산/거듭제곱만 허용하는 안전한 ast 기반 평가기.

``eval``/``exec`` 를 전혀 사용하지 않고, 허용된 노드 타입(상수/이항연산/단항연산)만
직접 순회하므로 임의 코드 실행 위험이 없다. 산술식 단계별 풀이와 행렬 리터럴
파싱에서 공유해서 사용한다.
"""
import ast

import sympy as sp

OPS = {
    ast.Add: ("+", lambda a, b: a + b),
    ast.Sub: ("-", lambda a, b: a - b),
    ast.Mult: ("×", lambda a, b: a * b),
    ast.Div: ("÷", lambda a, b: a / b),
    ast.Pow: ("^", lambda a, b: a**b),
}


def fmt(value) -> str:
    return str(sp.nsimplify(value))


def _to_rational(node: ast.Constant) -> sp.Rational:
    value = node.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("숫자가 아닌 값이 포함되어 있습니다.")
    if isinstance(value, int):
        return sp.Integer(value)
    return sp.Rational(str(value))


def eval_node(node, steps: list = None):
    """허용된 ast 노드만 재귀적으로 계산한다. steps 가 주어지면 계산 과정을 기록한다."""
    if isinstance(node, ast.Expression):
        return eval_node(node.body, steps)

    if isinstance(node, ast.Constant):
        return _to_rational(node)

    if isinstance(node, ast.UnaryOp):
        value = eval_node(node.operand, steps)
        if isinstance(node.op, ast.USub):
            result = -value
            if steps is not None:
                steps.append(f"-{fmt(value)} = {fmt(result)}")
            return result
        if isinstance(node.op, ast.UAdd):
            return value
        raise ValueError("지원하지 않는 연산입니다.")

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in OPS:
            raise ValueError("지원하지 않는 연산입니다.")
        symbol, func = OPS[op_type]
        left_val = eval_node(node.left, steps)
        right_val = eval_node(node.right, steps)
        if op_type is ast.Div and right_val == 0:
            raise ValueError("0으로 나눌 수 없습니다.")
        result = func(left_val, right_val)
        if steps is not None:
            steps.append(f"{fmt(left_val)} {symbol} {fmt(right_val)} = {fmt(result)}")
        return result

    raise ValueError("지원하지 않는 수식입니다. 숫자와 + - * / ^ () 만 사용할 수 있습니다.")


def parse_numeric_ast(text: str) -> ast.AST:
    cleaned = text.replace("×", "*").replace("÷", "/").replace("^", "**").strip()
    if not cleaned:
        raise ValueError("빈 입력입니다.")
    try:
        return ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"'{text}'을(를) 수식으로 해석할 수 없습니다.") from exc
