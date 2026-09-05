"""괄호/연산자 우선순위를 지키는 산술식 단계별 풀이.

``mathgrader.safe_arith`` 의 화이트리스트 ast 평가기를 사용한다. 자식 노드부터
계산하는 후위 순회이므로, 괄호와 연산자 우선순위가 이미 파이썬 ast 트리 구조에
반영되어 있어 표준적인 "연산 순서"를 그대로 따르게 된다.
"""
import sympy as sp

from ..safe_arith import eval_node, fmt, parse_numeric_ast


def arithmetic_steps(text: str):
    """산술식을 단계별로 계산한다. (steps, 최종값) 을 반환한다."""
    tree = parse_numeric_ast(text)
    steps: list = []
    value = eval_node(tree, steps)
    if not steps:
        steps.append(f"{fmt(value)} (계산할 연산이 없습니다)")
    return steps, sp.nsimplify(value)
